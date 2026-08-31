"""Cross-component contracts for frozen dynamic session skills."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.models.config import ProjectConfig
from wade.models.skill import SkillSlot
from wade.models.workflow import DelegationKind, SessionKind
from wade.services.session_composition_service import (
    SessionCompositionError,
    compose_session,
)
from wade.services.skill_invocation_service import (
    SkillInvocationError,
    cleanup_delegation_bundle,
    prepare_delegation_method,
)
from wade.skills.materializer import compute_session_bundle_digest


def _skill(
    root: Path,
    tool_root: str,
    name: str,
    method: str,
    *,
    resource: str | None = None,
) -> Path:
    directory = root / tool_root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} method\n---\n\n{method}\n",
        encoding="utf-8",
    )
    if resource is not None:
        (directory / "reference").mkdir()
        (directory / "reference/guide.md").write_text(resource, encoding="utf-8")
    return directory


def test_custom_methods_cannot_replace_fixed_implementation_steps(
    tmp_git_repo: Path,
) -> None:
    _skill(
        tmp_git_repo,
        ".agents/skills",
        "domain-implementation",
        "Model invariants before editing and verify boundary behavior.",
    )
    _skill(
        tmp_git_repo,
        ".claude/skills",
        "security-review",
        "Trace trust boundaries and report concrete exploit paths.",
    )

    result = compose_session(
        tmp_git_repo,
        tmp_git_repo,
        ProjectConfig(),
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        work_skills=["project:domain-implementation"],
        review_skills=["project:security-review"],
    )

    workflow = (tmp_git_repo / ".wade/session/WORKFLOW.md").read_text(encoding="utf-8")
    assert "wade review implementation" in workflow
    assert "wade implementation-session done" in workflow
    assert "Documentation [mandatory decision]" in workflow
    assert "Model invariants before editing" not in workflow
    assert "Trace trust boundaries" not in workflow
    assert (
        result.manifest.bindings[SkillSlot.WORK].skills[0].canonical_ref
        == "project:domain-implementation"
    )
    assert (
        result.manifest.bindings[SkillSlot.REVIEW].skills[0].canonical_ref
        == "project:security-review"
    )


def test_main_only_skill_is_copied_with_resources_and_frozen_until_refresh(
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    main.mkdir()
    worktree.mkdir()
    source = _skill(
        main,
        ".agents/skills",
        "local-method",
        "First version.",
        resource="original resource",
    )
    config = ProjectConfig()

    first = compose_session(
        worktree,
        main,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        work_skills=["project:local-method"],
    )
    snapshot = worktree / ".wade/session/skills/project/agents-skills/local-method"
    assert (snapshot / "reference/guide.md").read_text(encoding="utf-8") == "original resource"

    (source / "SKILL.md").write_text(
        "---\nname: local-method\ndescription: changed\n---\n\nSecond version.\n",
        encoding="utf-8",
    )
    resumed = compose_session(
        worktree,
        main,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
    )
    assert resumed.reused is True
    assert (
        resumed.manifest.bindings[SkillSlot.WORK].digest
        == first.manifest.bindings[SkillSlot.WORK].digest
    )
    assert "First version." in (snapshot / "SKILL.md").read_text(encoding="utf-8")

    with pytest.raises(SessionCompositionError, match="frozen"):
        compose_session(
            worktree,
            main,
            config,
            kind=SessionKind.IMPLEMENTATION,
            task_id="42",
            work_skills=["project:local-method"],
        )

    refreshed = compose_session(
        worktree,
        main,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        work_skills=["project:local-method"],
        refresh=True,
    )
    assert (
        refreshed.manifest.bindings[SkillSlot.WORK].digest
        != first.manifest.bindings[SkillSlot.WORK].digest
    )
    assert "Second version." in (snapshot / "SKILL.md").read_text(encoding="utf-8")
    assert "Second version." in (source / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative_path",
    [
        "WORKFLOW.md",
        "reference/recovery.md",
        "skills/builtin/implementation/SKILL.md",
    ],
)
def test_resume_rejects_modified_physical_session_bundle(
    tmp_git_repo: Path,
    relative_path: str,
) -> None:
    config = ProjectConfig()
    initial = compose_session(
        tmp_git_repo,
        tmp_git_repo,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
    )
    assert initial.manifest.bundle_digest == compute_session_bundle_digest(
        tmp_git_repo / ".wade/session"
    )
    target = tmp_git_repo / ".wade/session" / relative_path
    original = target.read_bytes()
    target.write_bytes(original + b"\nmodified after materialization\n")

    with pytest.raises(SessionCompositionError, match="integrity validation"):
        compose_session(
            tmp_git_repo,
            tmp_git_repo,
            config,
            kind=SessionKind.IMPLEMENTATION,
            task_id="42",
        )

    refreshed = compose_session(
        tmp_git_repo,
        tmp_git_repo,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        refresh=True,
    )
    assert refreshed.reused is False
    assert target.read_bytes() == original


@pytest.mark.parametrize("corruption", ["missing", "extra", "symlink"])
def test_resume_rejects_structurally_corrupted_session_bundle(
    tmp_git_repo: Path,
    corruption: str,
) -> None:
    config = ProjectConfig()
    compose_session(
        tmp_git_repo,
        tmp_git_repo,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
    )
    bundle = tmp_git_repo / ".wade/session"
    if corruption == "missing":
        (bundle / "WORKFLOW.md").unlink()
    elif corruption == "extra":
        (bundle / "unexpected.md").write_text("unexpected\n", encoding="utf-8")
    else:
        (bundle / "unexpected-link").symlink_to("WORKFLOW.md")

    with pytest.raises(SessionCompositionError, match="integrity validation"):
        compose_session(
            tmp_git_repo,
            tmp_git_repo,
            config,
            kind=SessionKind.IMPLEMENTATION,
            task_id="42",
        )

    refreshed = compose_session(
        tmp_git_repo,
        tmp_git_repo,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        refresh=True,
    )
    assert refreshed.reused is False


def test_mapped_review_rejects_modified_frozen_review_skill(tmp_git_repo: Path) -> None:
    config = ProjectConfig()
    compose_session(
        tmp_git_repo,
        tmp_git_repo,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
    )
    review_skill = tmp_git_repo / ".wade/session/skills/builtin/code-review/SKILL.md"
    review_skill.write_text("modified review method\n", encoding="utf-8")

    with pytest.raises(SkillInvocationError, match="integrity validation"):
        prepare_delegation_method(
            config,
            DelegationKind.CODE_REVIEW,
            cwd=tmp_git_repo,
        )


def test_ordered_binding_and_worktree_precedence_are_preserved(tmp_path: Path) -> None:
    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    main.mkdir()
    worktree.mkdir()
    _skill(main, ".agents/skills", "first", "Main checkout version.")
    _skill(worktree, ".agents/skills", "first", "Worktree version.")
    _skill(main, ".claude/skills", "second", "Main-only method.")

    result = compose_session(
        worktree,
        main,
        ProjectConfig(),
        kind=SessionKind.PLAN,
        task_id=None,
        work_skills=["project:second", "project:first"],
    )

    skills = result.manifest.bindings[SkillSlot.WORK].skills
    assert tuple(skill.canonical_ref for skill in skills) == (
        "project:second",
        "project:first",
    )
    first_snapshot = worktree / skills[1].materialized_path / "SKILL.md"
    assert "Worktree version." in first_snapshot.read_text(encoding="utf-8")


def test_ambiguous_project_name_fails_before_session_replacement(
    tmp_path: Path,
) -> None:
    _skill(tmp_path, ".agents/skills", "duplicate", "Agents method.")
    _skill(tmp_path, ".claude/skills", "duplicate", "Claude method.")

    with pytest.raises(SessionCompositionError, match="ambiguous"):
        compose_session(
            tmp_path,
            tmp_path,
            ProjectConfig(),
            kind=SessionKind.PLAN,
            task_id=None,
            work_skills=["project:duplicate"],
        )

    assert not (tmp_path / ".wade/session").exists()


def test_session_transition_replaces_manifest_and_mapped_review_uses_it(
    tmp_git_repo: Path,
) -> None:
    _skill(
        tmp_git_repo,
        ".agents/skills",
        "review-one",
        "Review the smallest observable behavior first.",
    )
    _skill(
        tmp_git_repo,
        ".agents/skills",
        "review-two",
        "Review data-flow and authorization boundaries first.",
    )
    config = ProjectConfig()
    compose_session(
        tmp_git_repo,
        tmp_git_repo,
        config,
        kind=SessionKind.IMPLEMENTATION,
        task_id="42",
        review_skills=["project:review-one"],
    )

    review_session = compose_session(
        tmp_git_repo,
        tmp_git_repo,
        config,
        kind=SessionKind.REVIEW_PR_COMMENTS,
        task_id="42",
        review_skills=["project:review-two"],
    )
    prepared = prepare_delegation_method(
        config,
        DelegationKind.CODE_REVIEW,
        cwd=tmp_git_repo,
    )

    assert review_session.manifest.session is SessionKind.REVIEW_PR_COMMENTS
    assert prepared.operation_bundle is None
    assert prepared.host_session is SessionKind.REVIEW_PR_COMMENTS
    assert prepared.binding == review_session.manifest.bindings[SkillSlot.REVIEW]
    assert "authorization boundaries" in prepared.method_section
    assert (
        f'root="{tmp_git_repo.as_posix()}/.wade/session/'
        'skills/project/agents-skills/review-two"' in prepared.method_section
    )


def test_foreign_dependency_method_preserves_host_session_bundle(
    tmp_git_repo: Path,
) -> None:
    config = ProjectConfig()
    compose_session(
        tmp_git_repo,
        tmp_git_repo,
        config,
        kind=SessionKind.PLAN,
        task_id="42",
    )
    manifest_path = tmp_git_repo / ".wade/session/manifest.json"
    before = manifest_path.read_bytes()

    prepared = prepare_delegation_method(
        config,
        DelegationKind.DEPENDENCY_ANALYSIS,
        cwd=tmp_git_repo,
    )
    try:
        assert prepared.host_session is None
        assert prepared.operation_bundle is not None
        assert manifest_path.read_bytes() == before
    finally:
        cleanup_delegation_bundle(prepared, preserve=False)

    assert manifest_path.read_bytes() == before


def test_mapped_review_fails_closed_on_unreadable_session_while_foreign_ops_continue(
    tmp_git_repo: Path,
) -> None:
    session = tmp_git_repo / ".wade/session"
    session.mkdir(parents=True)
    (session / "manifest.json").write_text("{truncated", encoding="utf-8")
    config = ProjectConfig()

    with pytest.raises(SkillInvocationError, match="unreadable or invalid"):
        prepare_delegation_method(config, DelegationKind.CODE_REVIEW, cwd=tmp_git_repo)

    dependency = prepare_delegation_method(
        config,
        DelegationKind.DEPENDENCY_ANALYSIS,
        cwd=tmp_git_repo,
    )
    assert dependency.host_session is None
    assert dependency.operation_bundle is not None
    cleanup_delegation_bundle(dependency, preserve=False)
