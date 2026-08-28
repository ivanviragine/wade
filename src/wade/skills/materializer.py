"""Immutable session and operation skill snapshot construction."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path

from wade.models.session_manifest import DelegationManifest, ResolvedBinding, SessionManifest
from wade.models.skill import ResolvedSkill, SkillRef, SkillSlot
from wade.models.workflow import (
    DELEGATION_DEFINITIONS,
    SESSION_DEFINITIONS,
    DelegationKind,
    SessionKind,
)
from wade.skills.discovery import SkillInventory, project_materialized_subpath
from wade.skills.resolver import SkillSelection, resolve_skill_refs
from wade.skills.validation import copy_inspected_skill, inspect_skill
from wade.utils.templates import get_skills_templates_dir, get_workflows_templates_dir

MAX_INTERACTIVE_LAUNCH_CHARS = 2_000
MAX_WORKFLOW_CHARS = 12_000
MAX_ACTIVE_WORK_SKILL_CHARS = 6_000
MAX_ALWAYS_READ_CHARS = 20_000
MAX_CATALOG_CHARS = 4_000


class SkillMaterializationError(RuntimeError):
    """A validated skill bundle could not be constructed transactionally."""


def _session_bundle_files(
    directory: Path,
    *,
    relative_prefix: Path = Path(),
) -> list[tuple[str, Path]]:
    """Collect physical bundle files without following any symlink."""

    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise SkillMaterializationError(
            f"Cannot enumerate session bundle {directory}: {exc}"
        ) from exc

    collected: list[tuple[str, Path]] = []
    for entry in entries:
        relative = relative_prefix / entry.name
        if not relative_prefix.parts and entry.name == "manifest.json":
            continue
        try:
            mode = entry.lstat().st_mode
        except OSError as exc:
            raise SkillMaterializationError(
                f"Cannot inspect session bundle entry {entry}: {exc}"
            ) from exc
        if stat.S_ISDIR(mode):
            collected.extend(_session_bundle_files(entry, relative_prefix=relative))
        elif stat.S_ISREG(mode):
            collected.append((relative.as_posix(), entry))
        else:
            raise SkillMaterializationError(
                f"Session bundle entries must be physical files or directories: {entry}"
            )
    return collected


def compute_session_bundle_digest(bundle_root: Path) -> str:
    """Hash every physical session file except the self-describing manifest."""

    try:
        root_mode = bundle_root.lstat().st_mode
    except OSError as exc:
        raise SkillMaterializationError(
            f"Cannot inspect session bundle {bundle_root}: {exc}"
        ) from exc
    if not stat.S_ISDIR(root_mode):
        raise SkillMaterializationError(
            f"Session bundle is not a physical directory: {bundle_root}"
        )

    digest = hashlib.sha256()
    for relative, source in _session_bundle_files(bundle_root):
        encoded_name = relative.encode("utf-8")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(source, flags)
            with os.fdopen(descriptor, "rb") as handle:
                file_stat = os.fstat(handle.fileno())
                if not stat.S_ISREG(file_stat.st_mode):
                    raise SkillMaterializationError(
                        f"Session bundle entry changed type during validation: {source}"
                    )
                digest.update(len(encoded_name).to_bytes(8, "big"))
                digest.update(encoded_name)
                digest.update(file_stat.st_size.to_bytes(8, "big"))
                observed_size = 0
                while chunk := handle.read(65_536):
                    observed_size += len(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise SkillMaterializationError(
                f"Cannot safely read session bundle entry {source}: {exc}"
            ) from exc
        if observed_size != file_stat.st_size:
            raise SkillMaterializationError(
                f"Session bundle entry changed while being validated: {source}"
            )
    return f"sha256:{digest.hexdigest()}"


def validate_session_bundle(worktree_root: Path, manifest: SessionManifest) -> None:
    """Verify frozen physical content against its manifest before reuse."""

    bundle_root = worktree_root / ".wade" / "session"
    observed_digest = compute_session_bundle_digest(bundle_root)
    if observed_digest != manifest.bundle_digest:
        raise SkillMaterializationError(
            "Active session bundle content does not match its frozen manifest"
        )

    prefix = ".wade/session/"
    checked: set[str] = set()
    for binding in manifest.bindings.values():
        for skill in binding.skills:
            if not skill.materialized_path.startswith(prefix):
                raise SkillMaterializationError(
                    "Invalid materialized skill path in session manifest: "
                    f"{skill.materialized_path}"
                )
            if skill.materialized_path in checked:
                continue
            checked.add(skill.materialized_path)
            inspected = inspect_skill(
                worktree_root / skill.materialized_path,
                project_root=bundle_root,
            )
            if inspected.digest != skill.content_digest or inspected.files != skill.files:
                raise SkillMaterializationError(
                    f"Materialized skill does not match its frozen manifest: {skill.canonical_ref}"
                )


def _ensure_owned_directory(root: Path, parts: tuple[str, ...]) -> Path:
    """Create a WADE-owned path without traversing symlinked components."""

    current = root
    for part in parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except OSError as exc:
                raise SkillMaterializationError(
                    f"Cannot create WADE-owned skill bundle directory {current}: {exc}"
                ) from exc
            continue
        except OSError as exc:
            raise SkillMaterializationError(
                f"Cannot inspect WADE-owned skill bundle directory {current}: {exc}"
            ) from exc
        if not stat.S_ISDIR(mode):
            raise SkillMaterializationError(
                f"Refusing unsafe WADE-owned skill bundle path: {current}"
            )
    return current


def _replace_directory(staging: Path, target: Path) -> None:
    """Replace a WADE-owned directory with rollback on rename failure."""

    try:
        target_mode = target.lstat().st_mode
    except FileNotFoundError:
        target_mode = None
    except OSError as exc:
        raise SkillMaterializationError(f"Cannot inspect session bundle {target}: {exc}") from exc
    if target_mode is not None and not stat.S_ISDIR(target_mode):
        raise SkillMaterializationError(f"Refusing unsafe existing session bundle: {target}")

    backup = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
    moved_old = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
    except OSError:
        if moved_old and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup)


def _copy_selection(
    selection: SkillSelection,
    bundle_root: Path,
    relative: Path,
) -> ResolvedSkill:
    destination = bundle_root / relative
    if not destination.exists():
        copy_inspected_skill(
            selection.skill.source,
            destination,
            project_root=selection.skill.project_root,
            inspected=selection.skill.inspected,
        )
    materialized = (Path(".wade/session") / relative).as_posix()
    return ResolvedSkill(
        canonical_ref=selection.ref.canonical,
        source_path=selection.skill.descriptor.source_path,
        materialized_path=materialized,
        content_digest=selection.skill.descriptor.content_digest,
        files=selection.skill.descriptor.files,
    )


def _selection_relative(selection: SkillSelection) -> Path:
    if selection.skill.origin == "builtin":
        return Path("skills/builtin") / selection.skill.descriptor.name
    return project_materialized_subpath(selection.skill)


def _render_list(skills: tuple[ResolvedSkill, ...], display_root: str) -> str:
    lines: list[str] = []
    for skill in skills:
        relative = skill.materialized_path.removeprefix(".wade/session").lstrip("/")
        lines.append(f"- `{display_root.rstrip('/')}/{relative}/SKILL.md`")
    return "\n".join(lines)


def _review_step(kind: SessionKind, enabled: bool) -> str:
    if not enabled:
        return "Skipped explicitly by project review configuration; record this step as skipped."
    if kind is SessionKind.PLAN:
        command = "`wade review plan <plan_file>` for each generated plan"
        prompt_completion = "Exit 2 requires performing the emitted self-review."
    else:
        command = "`wade review implementation` for the current commit"
        prompt_completion = (
            "Exit 2 requires performing the emitted self-review and then running "
            "`wade review implementation --ack-self-review`; prompt emission alone "
            "never writes a satisfying receipt."
        )
    return (
        f"Run {command}. The command loads only the frozen REVIEW methodology. "
        "Address actionable findings, commit changes, and re-review once after major fixes. "
        "The successful receipt must match the final pre-sync commit; if documentation, "
        "knowledge, or another later step creates a commit, repeat this review before done. "
        f"{prompt_completion} A successful external review writes the deterministic "
        "binding-aware receipt."
    )


def _workflow_partials() -> dict[str, str]:
    root = get_workflows_templates_dir() / "_partials"
    return {
        "{interaction_policy}": (root / "interaction-policy.md")
        .read_text(encoding="utf-8")
        .strip(),
        "{review_budget}": (root / "review-budget.md").read_text(encoding="utf-8").strip(),
        "{documentation_step}": (root / "documentation-step.md")
        .read_text(encoding="utf-8")
        .strip(),
        "{knowledge_step}": (root / "knowledge-step.md").read_text(encoding="utf-8").strip(),
        "{completion}": (root / "completion.md").read_text(encoding="utf-8").strip(),
    }


def render_workflow(
    kind: SessionKind,
    bindings: dict[SkillSlot, ResolvedBinding],
    *,
    review_enabled: bool,
    doc_targets: str,
    display_root: str = ".wade/session",
) -> str:
    """Render one fixed workflow without interpreting active skill content."""

    definition = SESSION_DEFINITIONS[kind]
    if definition.workflow_template is None:
        raise SkillMaterializationError(f"{kind.value} has no interactive workflow")
    source = get_workflows_templates_dir() / definition.workflow_template
    content = source.read_text(encoding="utf-8").strip()
    replacements = {
        **_workflow_partials(),
        "{work_skill_list}": _render_list(bindings[SkillSlot.WORK].skills, display_root),
        "{review_skill_list}": _render_list(bindings[SkillSlot.REVIEW].skills, display_root),
        "{review_step_state}": _review_step(kind, review_enabled),
        "{documentation_command}": (
            "review-pr-comments-session"
            if kind is SessionKind.REVIEW_PR_COMMENTS
            else "implementation-session"
        ),
        "{doc_targets}": doc_targets,
    }
    for placeholder, replacement in replacements.items():
        content = content.replace(placeholder, replacement)
    unresolved = [key for key in replacements if key in content]
    if unresolved:
        raise SkillMaterializationError(f"Unresolved workflow placeholders: {unresolved}")
    return content + "\n"


def materialize_session_bundle(
    worktree_root: Path,
    *,
    kind: SessionKind,
    task_id: str | None,
    refs: dict[SkillSlot, tuple[SkillRef, ...]],
    inventory: SkillInventory,
    review_enabled: bool,
    doc_targets: str,
    display_root: str = ".wade/session",
) -> SessionManifest:
    """Build and transactionally install a frozen interactive session bundle."""

    definition = SESSION_DEFINITIONS[kind]
    if definition.workflow_revision is None:
        raise SkillMaterializationError(f"{kind.value} is not an interactive session")
    selections = {
        slot: resolve_skill_refs(slot_refs, inventory) for slot, slot_refs in refs.items()
    }
    wade_dir = _ensure_owned_directory(worktree_root, (".wade",))
    staging = wade_dir / f".session.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        # Snapshot every available project skill. Availability is distinct from
        # activation, but remains reproducible for on-demand use during this session.
        for item in inventory.skills:
            destination = staging / project_materialized_subpath(item)
            copy_inspected_skill(
                item.source,
                destination,
                project_root=item.project_root,
                inspected=item.inspected,
            )

        resolved_bindings: dict[SkillSlot, ResolvedBinding] = {}
        for slot, slot_selections in selections.items():
            resolved = tuple(
                _copy_selection(selection, staging, _selection_relative(selection))
                for selection in slot_selections
            )
            resolved_bindings[slot] = ResolvedBinding.from_skills(resolved)

        work_skill_chars = sum(
            len(
                (
                    staging / skill.materialized_path.removeprefix(".wade/session/") / "SKILL.md"
                ).read_text(encoding="utf-8")
            )
            for skill in resolved_bindings[SkillSlot.WORK].skills
        )
        if work_skill_chars > MAX_ACTIVE_WORK_SKILL_CHARS:
            raise SkillMaterializationError(
                f"Active WORK skill text is {work_skill_chars} characters; "
                f"limit is {MAX_ACTIVE_WORK_SKILL_CHARS}"
            )

        # Fixed support instructions are never binding candidates.
        skills_templates = get_skills_templates_dir()
        package_root = skills_templates.parent.parent
        for name in definition.support_skills:
            source = skills_templates / name
            inspected = inspect_skill(source, project_root=package_root)
            copy_inspected_skill(
                source,
                staging / "support" / name,
                project_root=package_root,
                inspected=inspected,
            )

        workflow = render_workflow(
            kind,
            resolved_bindings,
            review_enabled=review_enabled,
            doc_targets=doc_targets,
            display_root=display_root,
        )
        if len(workflow) > MAX_WORKFLOW_CHARS:
            raise SkillMaterializationError(
                f"Rendered workflow is {len(workflow)} characters; limit is {MAX_WORKFLOW_CHARS}"
            )
        launch_prompt = (
            get_workflows_templates_dir().parent / "prompts" / str(definition.launch_prompt)
        ).read_text(encoding="utf-8")
        if len(launch_prompt) > MAX_INTERACTIVE_LAUNCH_CHARS:
            raise SkillMaterializationError(
                f"Interactive launch prompt is {len(launch_prompt)} characters; "
                f"limit is {MAX_INTERACTIVE_LAUNCH_CHARS}"
            )
        always_read = len(launch_prompt) + len(workflow) + work_skill_chars
        if always_read > MAX_ALWAYS_READ_CHARS:
            raise SkillMaterializationError(
                f"Always-read session instructions are {always_read} characters; "
                f"limit is {MAX_ALWAYS_READ_CHARS}"
            )
        (staging / "WORKFLOW.md").write_text(workflow, encoding="utf-8")
        shutil.copytree(get_workflows_templates_dir() / "reference", staging / "reference")

        catalog_lines = ["# Available project skills", ""]
        if inventory.skills:
            for item in inventory.skills:
                path = f"{display_root.rstrip('/')}/{project_materialized_subpath(item).as_posix()}"
                description = item.descriptor.description or "No description provided."
                catalog_lines.append(
                    f"- **{item.descriptor.name}** — {description} "
                    f"(`{item.origin}:{item.descriptor.source_path}`; `{path}/SKILL.md`)"
                )
        else:
            catalog_lines.append("No project skills were discovered for this session.")
        catalog = "\n".join(catalog_lines) + "\n"
        if len(catalog) > MAX_CATALOG_CHARS:
            suffix = "\nCatalog truncated; use `wade skills list` for the full inventory.\n"
            catalog = catalog[: MAX_CATALOG_CHARS - len(suffix)].rstrip() + suffix
        (staging / "AVAILABLE_SKILLS.md").write_text(catalog, encoding="utf-8")

        manifest = SessionManifest(
            session=kind,
            workflow_revision=definition.workflow_revision,
            bundle_digest=compute_session_bundle_digest(staging),
            task_id=task_id,
            ai_command=definition.ai_command,
            bindings=resolved_bindings,
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _replace_directory(staging, wade_dir / "session")
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def materialize_delegation_bundle(
    worktree_root: Path,
    *,
    kind: DelegationKind,
    refs: tuple[SkillRef, ...],
    inventory: SkillInventory,
    host_session: SessionKind | None = None,
) -> tuple[DelegationManifest, Path]:
    """Build an operation-scoped bundle for a foreign bounded delegation."""

    definition = DELEGATION_DEFINITIONS[kind]
    invocation_id = uuid.uuid4().hex
    parent = _ensure_owned_directory(
        worktree_root,
        (".wade", "operations", kind.value),
    )
    target = parent / invocation_id
    staging = parent / f".{invocation_id}.staging"
    staging.mkdir()
    try:
        selections = resolve_skill_refs(refs, inventory)
        resolved: list[ResolvedSkill] = []
        for selection in selections:
            relative = _selection_relative(selection)
            destination = staging / relative
            copy_inspected_skill(
                selection.skill.source,
                destination,
                project_root=selection.skill.project_root,
                inspected=selection.skill.inspected,
            )
            resolved.append(
                ResolvedSkill(
                    canonical_ref=selection.ref.canonical,
                    source_path=selection.skill.descriptor.source_path,
                    materialized_path=(
                        Path(".wade/operations") / kind.value / invocation_id / relative
                    ).as_posix(),
                    content_digest=selection.skill.descriptor.content_digest,
                    files=selection.skill.descriptor.files,
                )
            )
        manifest = DelegationManifest(
            delegation=kind,
            invocation_id=invocation_id,
            host_session=host_session,
            ai_command=definition.ai_command,
            binding=ResolvedBinding.from_skills(tuple(resolved)),
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target)
        return manifest, target
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
