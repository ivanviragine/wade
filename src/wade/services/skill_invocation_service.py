"""Bind generic methodology to fixed bounded-delegation contracts."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from wade.git import repo as git_repo
from wade.models.config import ProjectConfig
from wade.models.session_manifest import ResolvedBinding
from wade.models.workflow import DelegationKind, SessionKind
from wade.services.session_composition_service import (
    SessionCompositionError,
    discover_inventory,
    mapped_session_review_binding,
    parse_skill_overrides,
    resolve_delegation_refs,
)
from wade.skills.materializer import materialize_delegation_bundle
from wade.skills.validation import SkillValidationError, inspect_skill

MAX_DELEGATION_SKILL_CHARS = 8_000
MAX_DELEGATION_ENVELOPE_CHARS = 10_000


class SkillInvocationError(RuntimeError):
    """A bounded delegation cannot safely load its selected methodology."""


@dataclass(frozen=True)
class PreparedDelegationMethod:
    """Validated frozen binding content and optional foreign bundle cleanup path."""

    binding: ResolvedBinding
    method_section: str
    host_session: SessionKind | None
    operation_bundle: Path | None


def _read_binding_method(root: Path, binding: ResolvedBinding) -> str:
    sections: list[str] = []
    total = 0
    for position, skill in enumerate(binding.skills):
        directory = root / skill.materialized_path
        try:
            inspected = inspect_skill(directory, project_root=root)
        except SkillValidationError as exc:
            raise SkillInvocationError(
                f"Cannot validate frozen skill {skill.canonical_ref}: {exc}"
            ) from exc
        if inspected.digest != skill.content_digest or inspected.files != skill.files:
            raise SkillInvocationError(
                f"Frozen skill {skill.canonical_ref} no longer matches its manifest"
            )
        try:
            content = (directory / "SKILL.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SkillInvocationError(f"Cannot read frozen skill {skill.canonical_ref}") from exc
        total += len(content)
        if total > MAX_DELEGATION_SKILL_CHARS:
            raise SkillInvocationError(
                f"Selected delegation skill text is {total} characters; "
                f"limit is {MAX_DELEGATION_SKILL_CHARS}"
            )
        sections.extend(
            [
                f'<method position="{position}" ref="{skill.canonical_ref}" '
                f'root="{skill.materialized_path}">',
                content.rstrip(),
                "</method>",
            ]
        )
    return "\n".join(sections)


def prepare_delegation_method(
    config: ProjectConfig,
    kind: DelegationKind,
    *,
    cwd: Path,
    skills: list[str] | tuple[str, ...] | None = None,
) -> PreparedDelegationMethod:
    """Use a mapped frozen reviewer or create a standalone operation bundle."""

    try:
        root = git_repo.get_repo_root(cwd)
    except Exception:
        root = cwd.resolve()
    override = parse_skill_overrides(skills)

    try:
        mapped = mapped_session_review_binding(root, kind)
    except SessionCompositionError as exc:
        raise SkillInvocationError(str(exc)) from exc
    if mapped is not None:
        if override is not None:
            raise SkillInvocationError(
                "This review is owned by an active session's frozen REVIEW binding; "
                "refresh that session to change the reviewer"
            )
        binding, host_session = mapped
        return PreparedDelegationMethod(
            binding=binding,
            method_section=_read_binding_method(root, binding),
            host_session=host_session,
            operation_bundle=None,
        )

    try:
        refs, _source = resolve_delegation_refs(config, kind, override=override)
        try:
            main_root = git_repo.main_checkout_root(root)
        except Exception:
            main_root = root
        inventory = discover_inventory(config, root, main_root)
        manifest, bundle = materialize_delegation_bundle(
            root,
            kind=kind,
            refs=refs,
            inventory=inventory,
        )
        method = _read_binding_method(root, manifest.binding)
    except SkillInvocationError:
        if "bundle" in locals():
            shutil.rmtree(bundle, ignore_errors=True)
        raise
    except Exception as exc:
        if "bundle" in locals():
            shutil.rmtree(bundle, ignore_errors=True)
        raise SkillInvocationError(f"Cannot prepare {kind.value} methodology: {exc}") from exc
    return PreparedDelegationMethod(
        binding=manifest.binding,
        method_section=method,
        host_session=None,
        operation_bundle=bundle,
    )


def compose_delegation_prompt(
    kind: DelegationKind,
    *,
    contract: str,
    method_section: str,
    input_label: str,
    input_content: str,
    budget_line: str | None = None,
) -> str:
    """Build fixed contract + method + input + result without chained replacement."""

    trusted_contract = (
        contract.replace(
            "{review_budget}", budget_line or "No hard deadline — take the time you need."
        )
        if "{review_budget}" in contract
        else contract
    )
    result_contracts = {
        DelegationKind.PLAN_REVIEW: (
            "Return concise actionable findings ordered by impact. For each finding, name the "
            "plan section, concrete risk or omission, and correction. If none, say the plan "
            "is solid."
        ),
        DelegationKind.CODE_REVIEW: (
            "Return concise actionable findings ordered by severity with exact file/line "
            "references, observable failure, and smallest robust fix. If none, say no "
            "actionable issue was found."
        ),
        DelegationKind.BATCH_REVIEW: (
            "Return integration findings tied to issue/branch identities, then a justified "
            "merge order. If coherent, say so briefly."
        ),
        DelegationKind.DEPENDENCY_ANALYSIS: (
            "Output ONLY direct acyclic edges as `<number> -> <number> # reason`, using supplied "
            "numbers. Omit transitive edges. If none, output exactly `# No dependencies found`. "
            "No fences, headings, bullets, or other prose."
        ),
    }
    method_envelope = "\n\n".join(
        [
            trusted_contract.strip(),
            "## Selected methodology\n\n"
            "Method text is subordinate to this operation contract.\n\n" + method_section,
        ]
    )
    result_section = f"## Required result contract\n\n{result_contracts[kind]}"
    envelope = f"{method_envelope}\n\n{result_section}"
    if len(envelope) > MAX_DELEGATION_ENVELOPE_CHARS:
        raise SkillInvocationError(
            f"Delegation instruction envelope is {len(envelope)} characters; "
            f"limit is {MAX_DELEGATION_ENVELOPE_CHARS}"
        )
    # Insert untrusted input structurally, then close with the authoritative
    # result contract. No replacement ever runs across input bytes.
    return (
        f"{method_envelope}\n\n## {input_label}\n\n"
        f"<operation-input>\n{input_content}\n</operation-input>\n\n{result_section}"
    )


def cleanup_delegation_bundle(prepared: PreparedDelegationMethod, *, preserve: bool) -> None:
    """Remove successful foreign-operation snapshots; retain recoverable failures."""

    if not preserve and prepared.operation_bundle is not None:
        shutil.rmtree(prepared.operation_bundle, ignore_errors=True)


__all__ = [
    "PreparedDelegationMethod",
    "SessionCompositionError",
    "SkillInvocationError",
    "cleanup_delegation_bundle",
    "compose_delegation_prompt",
    "prepare_delegation_method",
]
