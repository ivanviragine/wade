"""Discovery, validation, resolution, and materialization contracts."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from wade.models.session_manifest import ResolvedBinding, SessionManifest
from wade.models.skill import ResolvedSkill, SkillRef, SkillSlot
from wade.models.workflow import SessionKind
from wade.skills import validation as skill_validation
from wade.skills.discovery import discover_project_skills
from wade.skills.materializer import (
    SkillMaterializationError,
    materialize_session_bundle,
)
from wade.skills.resolver import SkillResolutionError, resolve_skill_refs
from wade.skills.validation import SkillValidationError, inspect_skill


def _skill(root: Path, relative: str, name: str, body: str = "Use evidence.") -> Path:
    path = root / relative / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} method\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_digest_is_stable_and_changes_with_paths_or_content(tmp_path: Path) -> None:
    skill = _skill(tmp_path, ".agents/skills", "demo")
    (skill / "reference").mkdir()
    (skill / "reference/guide.md").write_text("guide\n", encoding="utf-8")

    first = inspect_skill(skill, project_root=tmp_path)
    second = inspect_skill(skill, project_root=tmp_path)
    assert first == second
    assert first.files == ("SKILL.md", "reference/guide.md")

    (skill / "reference/guide.md").write_text("changed\n", encoding="utf-8")
    assert inspect_skill(skill, project_root=tmp_path).digest != first.digest


def test_external_broken_and_cyclic_symlinks_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")

    external = _skill(tmp_path, ".agents/skills", "external")
    (external / "secret.md").symlink_to(outside / "secret.md")
    with pytest.raises(SkillValidationError, match="escapes"):
        inspect_skill(external, project_root=tmp_path)

    broken = _skill(tmp_path, ".agents/skills", "broken")
    (broken / "missing.md").symlink_to("not-there.md")
    with pytest.raises(SkillValidationError, match="Broken"):
        inspect_skill(broken, project_root=tmp_path)

    cyclic = _skill(tmp_path, ".agents/skills", "cyclic")
    (cyclic / "loop").symlink_to(".")
    with pytest.raises(SkillValidationError, match="cycle"):
        inspect_skill(cyclic, project_root=tmp_path)


def test_safe_internal_symlink_is_dereferenced_in_snapshot(tmp_path: Path) -> None:
    skill = _skill(tmp_path, ".agents/skills", "linked")
    (skill / "reference").mkdir()
    (skill / "reference/real.md").write_text("method detail", encoding="utf-8")
    (skill / "guide.md").symlink_to("reference/real.md")
    inventory = discover_project_skills(tmp_path, tmp_path)

    materialize_session_bundle(
        tmp_path,
        kind=SessionKind.PLAN,
        task_id=None,
        refs={
            SkillSlot.WORK: (SkillRef.model_validate("project:linked"),),
            SkillSlot.REVIEW: (SkillRef.model_validate("builtin:plan-review"),),
        },
        inventory=inventory,
        review_enabled=True,
        doc_targets="README.md",
    )

    copied = tmp_path / ".wade/session/skills/project/agents-skills/linked/guide.md"
    assert copied.is_file()
    assert not copied.is_symlink()
    assert copied.read_text(encoding="utf-8") == "method detail"


def test_skill_file_count_and_byte_limits_are_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    too_many = _skill(tmp_path, ".agents/skills", "too-many")
    (too_many / "extra.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(skill_validation, "MAX_SKILL_FILES", 1)
    with pytest.raises(SkillValidationError, match="files; limit"):
        inspect_skill(too_many, project_root=tmp_path)

    monkeypatch.setattr(skill_validation, "MAX_SKILL_FILES", 256)
    oversized = _skill(tmp_path, ".agents/skills", "oversized", "large method")
    monkeypatch.setattr(skill_validation, "MAX_FILE_BYTES", 4)
    with pytest.raises(SkillValidationError, match="per-file limit"):
        inspect_skill(oversized, project_root=tmp_path)

    monkeypatch.setattr(skill_validation, "MAX_FILE_BYTES", 1024 * 1024)
    monkeypatch.setattr(skill_validation, "MAX_SKILL_BYTES", 8)
    with pytest.raises(SkillValidationError, match="bytes; limit"):
        inspect_skill(oversized, project_root=tmp_path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_special_file_is_rejected(tmp_path: Path) -> None:
    skill = _skill(tmp_path, ".agents/skills", "special")
    os.mkfifo(skill / "pipe")
    with pytest.raises(SkillValidationError, match="Special"):
        inspect_skill(skill, project_root=tmp_path)


def test_discovery_merges_worktree_and_main_and_deduplicates_symlinked_roots(
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    main.mkdir()
    worktree.mkdir()
    _skill(worktree, ".agents/skills", "tracked", "worktree version")
    _skill(main, ".agents/skills", "tracked", "main version")
    _skill(main, ".claude/skills", "local-only")
    (main / ".cursor").mkdir()
    (main / ".cursor/skills").symlink_to(Path("../.claude/skills"))

    inventory = discover_project_skills(worktree, main)
    by_name = {item.descriptor.name: item for item in inventory.skills}
    assert set(by_name) == {"tracked", "local-only"}
    assert by_name["tracked"].origin == "worktree"
    assert by_name["local-only"].origin == "main"


def test_discovery_does_not_treat_crossby_scene_projection_as_full_inventory(
    tmp_path: Path,
) -> None:
    _skill(tmp_path, ".agents/skills", "selected")
    _skill(tmp_path, ".agents/skills", "outside-scene")
    projection = tmp_path / ".crossby/scene/active/skills"
    projection.mkdir(parents=True)
    (projection / "selected").symlink_to(Path("../../../../.agents/skills/selected"))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/skills").symlink_to(Path("../.crossby/scene/active/skills"))

    inventory = discover_project_skills(tmp_path, tmp_path)

    assert {skill.descriptor.name for skill in inventory.skills} == {
        "selected",
        "outside-scene",
    }
    assert any("scene projection" in warning for warning in inventory.warnings)


def test_include_exclude_and_same_name_collision_resolution(tmp_path: Path) -> None:
    _skill(tmp_path, ".agents/skills", "same", "agents")
    _skill(tmp_path, ".claude/skills", "same", "claude")
    _skill(tmp_path, ".claude/skills", "ignored")
    inventory = discover_project_skills(tmp_path, tmp_path, exclude=("ignored",))

    with pytest.raises(SkillResolutionError, match="ambiguous"):
        resolve_skill_refs((SkillRef.model_validate("project:same"),), inventory)
    explicit = resolve_skill_refs((SkillRef.model_validate("path:.agents/skills/same"),), inventory)
    assert explicit[0].skill.descriptor.source_path == ".agents/skills/same"
    with pytest.raises(SkillResolutionError, match="not found"):
        resolve_skill_refs((SkillRef.model_validate("project:ignored"),), inventory)


@pytest.mark.parametrize(
    ("include", "exclude"),
    [
        (("selected",), ()),
        (("*",), ("invalid",)),
    ],
)
def test_filters_are_applied_before_skill_validation(
    tmp_path: Path,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> None:
    _skill(tmp_path, ".agents/skills", "selected")
    invalid = _skill(tmp_path, ".agents/skills", "invalid")
    (invalid / "broken.md").symlink_to("missing.md")

    inventory = discover_project_skills(
        tmp_path,
        tmp_path,
        include=include,
        exclude=exclude,
    )

    assert [skill.descriptor.name for skill in inventory.skills] == ["selected"]


def test_selected_skill_still_fails_validation(tmp_path: Path) -> None:
    invalid = _skill(tmp_path, ".agents/skills", "invalid")
    (invalid / "broken.md").symlink_to("missing.md")

    with pytest.raises(SkillValidationError, match="Broken"):
        discover_project_skills(tmp_path, tmp_path)


def test_self_init_template_links_are_not_discovered_as_project_skills(
    tmp_path: Path,
) -> None:
    # Point a tool-native root at this checkout's packaged templates, matching
    # WADE's developer worktree topology.
    templates = Path(__file__).resolve().parents[3] / "templates/skills"
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents/skills").symlink_to(templates)

    assert discover_project_skills(tmp_path, tmp_path).skills == ()


def test_target_project_may_discover_its_own_templates_skill_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skill(tmp_path, "templates/skills", "project-template")
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents/skills").symlink_to(Path("../templates/skills"))
    monkeypatch.setattr(
        "wade.skills.discovery.get_wade_repo_root",
        lambda: tmp_path.parent / "different-wade-checkout",
    )

    inventory = discover_project_skills(tmp_path, tmp_path)

    assert [skill.descriptor.name for skill in inventory.skills] == ["project-template"]


def test_self_init_builtin_refs_freeze_live_worktree_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    main.mkdir()
    worktree.mkdir()
    templates = Path(__file__).resolve().parents[3] / "templates" / "skills"
    shutil.copytree(templates, worktree / "templates" / "skills")
    implementation = worktree / "templates/skills/implementation/SKILL.md"
    implementation.write_text(
        implementation.read_text(encoding="utf-8") + "\nUse the live branch method.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("wade.skills.discovery.get_wade_repo_root", lambda: main)

    inventory = discover_project_skills(worktree, main)
    selection = resolve_skill_refs((SkillRef.model_validate("builtin:implementation"),), inventory)[
        0
    ]

    assert inventory.builtin_templates == worktree / "templates/skills"
    assert selection.skill.source == worktree / "templates/skills/implementation"
    assert (
        selection.skill.inspected.digest
        == inspect_skill(selection.skill.source, project_root=worktree).digest
    )


def test_composite_binding_digest_is_order_sensitive() -> None:
    first = ResolvedSkill(
        canonical_ref="builtin:implementation",
        source_path="templates/skills/implementation",
        materialized_path=".wade/session/skills/builtin/implementation",
        content_digest="sha256:" + "1" * 64,
        files=("SKILL.md",),
    )
    second = ResolvedSkill(
        canonical_ref="project:custom",
        source_path=".agents/skills/custom",
        materialized_path=".wade/session/skills/project/agents-skills/custom",
        content_digest="sha256:" + "2" * 64,
        files=("SKILL.md",),
    )
    assert (
        ResolvedBinding.from_skills((first, second)).digest
        != ResolvedBinding.from_skills((second, first)).digest
    )


def test_session_bundle_snapshots_inventory_and_active_bindings(tmp_path: Path) -> None:
    project_skill = _skill(tmp_path, ".agents/skills", "custom")
    (project_skill / "reference").mkdir()
    (project_skill / "reference/details.md").write_text("details", encoding="utf-8")
    inventory = discover_project_skills(tmp_path, tmp_path)

    manifest = materialize_session_bundle(
        tmp_path,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        refs={
            SkillSlot.WORK: (SkillRef.model_validate("project:custom"),),
            SkillSlot.REVIEW: (SkillRef.model_validate("builtin:code-review"),),
        },
        inventory=inventory,
        review_enabled=True,
        doc_targets="README.md and docs/",
    )

    session = tmp_path / ".wade/session"
    loaded = SessionManifest.model_validate_json((session / "manifest.json").read_text())
    assert loaded == manifest
    assert (session / "WORKFLOW.md").is_file()
    assert "wade review implementation" in (session / "WORKFLOW.md").read_text()
    assert (session / "skills/project/agents-skills/custom/reference/details.md").is_file()
    assert (session / "skills/builtin/code-review/SKILL.md").is_file()
    assert "custom" in (session / "AVAILABLE_SKILLS.md").read_text()
    assert json.loads((session / "manifest.json").read_text())["task_id"] == "42"


def test_refresh_replaces_only_session_and_preserves_operations_and_reviews(
    tmp_path: Path,
) -> None:
    inventory = discover_project_skills(tmp_path, tmp_path)
    refs = {
        SkillSlot.WORK: (SkillRef.model_validate("builtin:implementation"),),
        SkillSlot.REVIEW: (SkillRef.model_validate("builtin:code-review"),),
    }
    materialize_session_bundle(
        tmp_path,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        refs=refs,
        inventory=inventory,
        review_enabled=True,
        doc_targets="README.md",
    )
    operation = tmp_path / ".wade/operations/code-review/live/file"
    review = tmp_path / ".wade/reviews/receipt.json"
    operation.parent.mkdir(parents=True)
    review.parent.mkdir(parents=True)
    operation.write_text("live", encoding="utf-8")
    review.write_text("history", encoding="utf-8")

    materialize_session_bundle(
        tmp_path,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        refs=refs,
        inventory=inventory,
        review_enabled=True,
        doc_targets="README.md",
    )
    assert operation.read_text() == "live"
    assert review.read_text() == "history"


def test_materialization_refuses_symlinked_wade_state_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-state"
    outside.mkdir()
    (tmp_path / ".wade").symlink_to(outside, target_is_directory=True)
    inventory = discover_project_skills(tmp_path, tmp_path)

    with pytest.raises(SkillMaterializationError, match="unsafe"):
        materialize_session_bundle(
            tmp_path,
            kind=SessionKind.IMPLEMENTATION,
            task_id="42",
            refs={
                SkillSlot.WORK: (SkillRef.model_validate("builtin:implementation"),),
                SkillSlot.REVIEW: (SkillRef.model_validate("builtin:code-review"),),
            },
            inventory=inventory,
            review_enabled=True,
            doc_targets="README.md",
        )

    assert list(outside.iterdir()) == []
