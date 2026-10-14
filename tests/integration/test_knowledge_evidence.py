"""Committed evidence and ownership regressions using disposable Git repositories."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wade.models.config import KnowledgeConfig, ProjectConfig
from wade.services import knowledge_service as knowledge
from wade.services.implementation_service.bootstrap import _carry_forward_pending_votes


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout


@pytest.fixture
def evidence_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "main"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Knowledge integration fixture")
    git(repo, "config", "user.email", "knowledge@example.invalid")
    (repo / ".gitignore").write_text(".wade/\n")
    (repo / ".wade.yml").write_text(
        "version: 2\nproject:\n  main_branch: main\nknowledge:\n  enabled: true\n"
        "  path: KNOWLEDGE.md\n"
    )
    (repo / "source.py").write_text("answer = 42\n")
    (repo / "KNOWLEDGE.md").write_text(
        "# Project Knowledge\n\n## claim-a | 2026-10-14 | implementation\n\n"
        "The answer is forty two.\n\n---\n"
    )
    (repo / "KNOWLEDGE.ratings.jsonl").write_text("")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "feat: committed source and legacy claim fixture")
    return repo


def test_foreign_handoff_cannot_dirty_unrelated_reviewed_branch(evidence_repo: Path) -> None:
    main = evidence_repo
    other = main.parent / "task-532"
    git(main, "worktree", "add", "-b", "feat/532-unrelated", str(other))
    event = {
        "id": "claim-a",
        "dir": "up",
        "event_id": "foreign-event",
        "handoff_id": "plan-492",
        "ts": "2026-10-14T00:00:00Z",
    }
    with (main / "KNOWLEDGE.ratings.jsonl").open("a") as f:
        f.write(json.dumps(event) + "\n")
    before_main = (main / "KNOWLEDGE.ratings.jsonl").read_bytes()
    before_other = (other / "KNOWLEDGE.ratings.jsonl").read_bytes()
    before_head = git(other, "rev-parse", "HEAD")
    before_index = git(other, "diff", "--cached")
    _carry_forward_pending_votes(
        other, main, ProjectConfig(knowledge=KnowledgeConfig(enabled=True))
    )
    assert (main / "KNOWLEDGE.ratings.jsonl").read_bytes() == before_main
    assert (other / "KNOWLEDGE.ratings.jsonl").read_bytes() == before_other
    assert git(other, "rev-parse", "HEAD") == before_head
    assert git(other, "diff", "--cached") == before_index


def test_direct_detached_append_cannot_redirect_into_main(evidence_repo: Path) -> None:
    main = evidence_repo
    detached = main.parent / "plan-492"
    git(main, "worktree", "add", "--detach", str(detached))
    knowledge.mark_throwaway_knowledge_session(detached)
    before = (main / "KNOWLEDGE.md").read_bytes()
    with pytest.raises(ValueError, match=r"(?i)(owner|detached|implementation|tracked)"):
        knowledge.append_knowledge(detached, KnowledgeConfig(enabled=True), "foreign claim", "plan")
    assert (main / "KNOWLEDGE.md").read_bytes() == before


def test_legacy_stale_feedback_cannot_hide_unverified_claim(evidence_repo: Path) -> None:
    repo = evidence_repo
    (repo / "KNOWLEDGE.ratings.jsonl").write_text(
        '{"id":"claim-a","dir":"stale"}\n{"id":"claim-a","dir":"stale"}\n'
    )
    report = knowledge.get_annotated_knowledge(repo, KnowledgeConfig(enabled=True))
    assert report.entries_count == 1
    assert report.content is not None and "unverified" in report.content.lower()


@pytest.fixture
def owned_repo(evidence_repo: Path) -> Path:
    from wade.models.workflow import SessionKind
    from wade.services.session_composition_service import compose_session

    worktree = evidence_repo.parent / "owner-510"
    git(evidence_repo, "worktree", "add", "-b", "feat/510-owner", str(worktree))
    compose_session(
        worktree, evidence_repo, ProjectConfig(), kind=SessionKind.IMPLEMENTATION, task_id="510"
    )
    return worktree


def test_evidence_survives_receipt_commit_and_detects_source_drift(owned_repo: Path) -> None:
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    config = KnowledgeConfig(enabled=True)
    proof = check_entry(
        repo, config, "claim-a", "current", files=("source.py#L1-L1",), note="Read committed answer"
    )
    source_commit = git(repo, "rev-parse", "HEAD").strip()
    assert proof.evidence[0].checked_commit == source_commit
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: record checked answer")
    report = reconcile_knowledge(repo, config)
    assert report.entries[0].assessment == "checked-current"
    assert report.entries[0].publication == "integrated"
    assert report.entries[0].assessments[0].publication == "branch-local"
    assert report.entries[0].assessments[0].checks[0].historical
    (repo / "source.py").write_text("answer = 7\n")
    git(repo, "add", "source.py")
    git(repo, "commit", "-m", "fix: change answer")
    assert reconcile_knowledge(repo, config).entries[0].assessment == "needs-recheck"


@pytest.mark.parametrize("change", ["dirty", "staged", "untracked", "symlink", "missing"])
def test_check_rejects_uncommitted_or_nonordinary_sources(owned_repo: Path, change: str) -> None:
    from wade.services.knowledge_assessment import check_entry

    repo = owned_repo
    path = "source.py"
    if change in {"dirty", "staged"}:
        (repo / path).write_text("answer = 7\n")
        if change == "staged":
            git(repo, "add", path)
    elif change == "untracked":
        path = "other.py"
        (repo / path).write_text("answer = 42\n")
    elif change == "missing":
        path = "missing.py"
    else:
        path = "link.py"
        (repo / path).symlink_to("source.py")
        git(repo, "add", path)
        git(repo, "commit", "-m", "test: symlink fixture")
    before = (repo / "KNOWLEDGE.ratings.jsonl").read_bytes()
    with pytest.raises(ValueError):
        check_entry(
            repo,
            KnowledgeConfig(enabled=True),
            "claim-a",
            "current",
            files=(path,),
            note="must reject",
        )
    assert (repo / "KNOWLEDGE.ratings.jsonl").read_bytes() == before


def test_read_only_reconcile_preserves_head_index_and_working_bytes(owned_repo: Path) -> None:
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    (repo / "KNOWLEDGE.md").write_text((repo / "KNOWLEDGE.md").read_text() + "\nlocal draft\n")
    before = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "diff", "--cached"),
        git(repo, "status", "--porcelain"),
        (repo / "KNOWLEDGE.md").read_bytes(),
    )
    report = reconcile_knowledge(repo, KnowledgeConfig(enabled=True))
    assert report.local_dirty_paths
    assert report.entries[0].assessment == "unverified"
    after = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "diff", "--cached"),
        git(repo, "status", "--porcelain"),
        (repo / "KNOWLEDGE.md").read_bytes(),
    )
    assert before == after


def test_missing_historical_commit_does_not_invalidate_unchanged_blob(owned_repo: Path) -> None:
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    event = check_entry(
        repo,
        KnowledgeConfig(enabled=True),
        "claim-a",
        "current",
        files=("source.py",),
        note="source proof",
    )
    data = event.model_dump(mode="json")
    data["evidence"][0]["checked_commit"] = "f" * 40
    (repo / "KNOWLEDGE.ratings.jsonl").write_text(json.dumps(data) + "\n")
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "test: historical commit unavailable after squash")
    assert (
        reconcile_knowledge(repo, KnowledgeConfig(enabled=True)).entries[0].assessment
        == "checked-current"
    )


def test_exact_duplicates_normalize_idempotently_without_transferring_assessments(
    owned_repo: Path,
) -> None:
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    config = KnowledgeConfig(enabled=True)
    original = (repo / "KNOWLEDGE.md").read_text()
    duplicate = (
        original[original.index("## claim-a") :]
        .replace("claim-a", "claim-z")
        .replace("implementation", "plan | tags: different")
    )
    (repo / "KNOWLEDGE.md").write_text(original + "\n" + duplicate)
    git(repo, "add", "KNOWLEDGE.md")
    git(repo, "commit", "-m", "docs: exact duplicate with different metadata")
    check_entry(
        repo, config, "claim-z", "current", files=("source.py",), note="only this member checked"
    )
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: check one duplicate")
    report = reconcile_knowledge(repo, config, apply=True)
    assert report.normalization_events_appended == 1
    assert report.duplicate_groups[0].representative_id == "claim-a"
    assert report.entries[0].assessment == "unverified"
    assert report.entries[1].assessment == "checked-current"
    before = (repo / "KNOWLEDGE.ratings.jsonl").read_bytes()
    assert reconcile_knowledge(repo, config, apply=True).normalization_events_appended == 0
    assert (repo / "KNOWLEDGE.ratings.jsonl").read_bytes() == before
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: normalize duplicate")
    (repo / "KNOWLEDGE.md").write_text(
        (repo / "KNOWLEDGE.md")
        .read_text()
        .replace("## claim-z", "## claim-z", 1)
        .replace("The answer is forty two.", "Changed claim.", 1)
    )
    git(repo, "add", "KNOWLEDGE.md")
    git(repo, "commit", "-m", "docs: change original claim")
    assert not reconcile_knowledge(repo, config).duplicate_groups


def test_observer_proposal_does_not_dirty_author_or_main(
    owned_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.services.knowledge_assessment import feedback
    from wade.services.knowledge_proposals import proposals

    monkeypatch.setenv("WADE_KNOWLEDGE_OBSERVER", "1")
    monkeypatch.setenv("WADE_KNOWLEDGE_OPERATION_ID", "fixture-review-operation")
    before = git(owned_repo, "status", "--porcelain")
    identity = feedback(owned_repo, KnowledgeConfig(enabled=True), "claim-a", "useful")
    assert isinstance(identity, str)
    proposal = proposals(owned_repo)[0]
    assert proposal.owner.operation_id == "fixture-review-operation"
    assert proposal.owner.task_id == "510"
    assert git(owned_repo, "status", "--porcelain") == before


def test_compatible_checks_coexist_and_incompatible_outcomes_require_replacement(
    owned_repo: Path,
) -> None:
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    config = KnowledgeConfig(enabled=True)
    first = check_entry(
        repo, config, "claim-a", "current", files=("source.py",), note="first reader"
    )
    second = check_entry(
        repo, config, "claim-a", "current", files=("source.py#L1-L1",), note="second reader"
    )
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: compatible independent evidence")
    assert reconcile_knowledge(repo, config).entries[0].assessment == "checked-current"
    third = check_entry(
        repo, config, "claim-a", "contradicted", files=("source.py",), note="contradiction"
    )
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: incompatible concurrent outcome")
    assert reconcile_knowledge(repo, config).entries[0].assessment == "needs-recheck"
    check_entry(
        repo,
        config,
        "claim-a",
        "current",
        files=("source.py",),
        note="explicitly resolve all live observations",
        replaces=(first.event_id, second.event_id, third.event_id),
    )
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: resolve incompatible assessments")
    assert reconcile_knowledge(repo, config).entries[0].assessment == "checked-current"


def _completed_parent(main: Path, *, multi: bool = False) -> tuple[Path, str]:
    from wade.models.plan_bundle import BUNDLE_MARKER, PlanBundle, PlanMember
    from wade.models.workflow import SessionKind
    from wade.services import interactive_plan_service as interactive
    from wade.services.plan_service import _ensure_handoff_progress, _save_handoff_binding
    from wade.services.session_composition_service import compose_session

    parent = main.parent / "original-parent"
    git(main, "worktree", "add", "--detach", str(parent))
    config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True))
    compose_session(parent, main, config, kind=SessionKind.PLAN, task_id="510")
    interactive.begin(parent, "codex", review_required=False, knowledge_required=True)
    plan = "# fix: ownership fixture\n\n## Complexity\neasy\n\n## Tasks\n- Verify ownership.\n"
    members = (PlanMember(filename="PLAN.md", markdown=plan),)
    if multi:
        members = (
            PlanMember(filename="PLAN-one.md", markdown=plan),
            PlanMember(filename="PLAN-two.md", markdown=plan),
        )
    bundle = PlanBundle(plans=members, knowledge_votes=())
    interactive.import_artifact(
        parent, BUNDLE_MARKER + "\n```json\n" + bundle.model_dump_json() + "\n```\n"
    )
    interactive.complete(parent)
    state, _ = interactive.collect_with_state(parent)
    _save_handoff_binding(parent, None, config)
    progress = _ensure_handoff_progress(parent, state.session_id, None, config, ())
    if multi:
        from wade.services.plan_service import _save_handoff_progress

        progress.persisted_issues = {"PLAN-one.md": "510", "PLAN-two.md": "532"}
        assert _save_handoff_progress(parent, progress)
    return parent, state.session_id


def test_matching_parent_acceptance_survives_cleanup_and_grants_no_correctness(
    owned_repo: Path,
) -> None:
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_proposals import accept_proposal, bind_completed_parent, proposals
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    main = repo.parent / "main"
    parent, operation = _completed_parent(main)
    config = KnowledgeConfig(enabled=True)
    first = knowledge.record_handoff_rating_for_session(parent, config, "claim-a", "up", operation)
    retry = knowledge.record_handoff_rating_for_session(parent, config, "claim-a", "up", operation)
    assert first == retry
    proposal = proposals(repo)[0]
    with pytest.raises(ValueError, match="association"):
        accept_proposal(repo, config, proposal.proposal_id)
    bind_completed_parent(parent, operation)
    git(main, "worktree", "remove", "--force", str(parent))
    event = accept_proposal(repo, config, proposal.proposal_id)
    assert accept_proposal(repo, config, proposal.proposal_id) == event
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: accept matching historical observation")
    assert reconcile_knowledge(repo, config).entries[0].assessment == "unverified"
    check_entry(
        repo,
        config,
        "claim-a",
        "current",
        files=("source.py",),
        note="Fresh evidence after acceptance",
        proposal_id=proposal.proposal_id,
    )
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: assess accepted observation freshly")
    assert reconcile_knowledge(repo, config).entries[0].assessment == "checked-current"


def test_foreign_task_and_changed_claim_cannot_accept_parent_proposal(owned_repo: Path) -> None:
    from wade.models.workflow import SessionKind
    from wade.services.knowledge_proposals import accept_proposal, bind_completed_parent, proposals
    from wade.services.session_composition_service import compose_session

    repo = owned_repo
    main = repo.parent / "main"
    parent, operation = _completed_parent(main)
    config = KnowledgeConfig(enabled=True)
    knowledge.record_handoff_rating_for_session(parent, config, "claim-a", "up", operation)
    bind_completed_parent(parent, operation)
    proposal = proposals(repo)[0]
    foreign = main.parent / "foreign-532"
    git(main, "worktree", "add", "-b", "feat/532-foreign", str(foreign))
    compose_session(foreign, main, ProjectConfig(), kind=SessionKind.IMPLEMENTATION, task_id="532")
    before = (foreign / "KNOWLEDGE.ratings.jsonl").read_bytes()
    with pytest.raises(ValueError, match="ownership"):
        accept_proposal(foreign, config, proposal.proposal_id)
    assert (foreign / "KNOWLEDGE.ratings.jsonl").read_bytes() == before
    (repo / "KNOWLEDGE.md").write_text(
        (repo / "KNOWLEDGE.md").read_text().replace("forty two", "seven")
    )
    with pytest.raises(ValueError, match="content changed"):
        accept_proposal(repo, config, proposal.proposal_id)


def test_unspecified_multiplan_owner_stays_pending(owned_repo: Path) -> None:
    from wade.services.knowledge_proposals import accept_proposal, bind_completed_parent, proposals

    repo = owned_repo
    parent, operation = _completed_parent(repo.parent / "main", multi=True)
    config = KnowledgeConfig(enabled=True)
    knowledge.record_handoff_rating_for_session(parent, config, "claim-a", "up", operation)
    bind_completed_parent(parent, operation)
    proposal = proposals(repo)[0]
    with pytest.raises(ValueError, match="association"):
        accept_proposal(repo, config, proposal.proposal_id)
    assert not (repo / "KNOWLEDGE.ratings.jsonl").read_text()


@pytest.mark.parametrize("mode", ["main", "detached", "missing-session"])
def test_direct_services_refuse_unowned_tracked_mutation(evidence_repo: Path, mode: str) -> None:
    repo = evidence_repo
    if mode != "main":
        worktree = repo.parent / mode
        args = ("--detach",) if mode == "detached" else ("-b", "feat/no-session")
        git(repo, "worktree", "add", *args, str(worktree))
        repo = worktree
    before = (repo / "KNOWLEDGE.md").read_bytes()
    with pytest.raises(ValueError, match="Tracked knowledge writes"):
        knowledge.append_knowledge(repo, KnowledgeConfig(enabled=True), "unowned", "implementation")
    assert (repo / "KNOWLEDGE.md").read_bytes() == before
    with pytest.raises(ValueError, match="Tracked knowledge writes"):
        knowledge.record_rating(repo / "KNOWLEDGE.ratings.jsonl", "claim-a", "up")


def test_explicit_native_refresh_preserves_artifacts_and_requires_new_review(
    evidence_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.models.workflow import SESSION_DEFINITIONS, SessionKind
    from wade.services import interactive_plan_service as interactive

    with monkeypatch.context() as old:
        old.setitem(
            SESSION_DEFINITIONS,
            SessionKind.PLAN,
            SESSION_DEFINITIONS[SessionKind.PLAN].model_copy(update={"workflow_revision": 3}),
        )
        parent, identity = _completed_parent(evidence_repo)
    plan = parent / ".wade/plans/PLAN.md"
    before = plan.read_bytes()
    progress = (parent / ".wade/plans/handoff-progress.json").read_bytes()
    interactive.refresh_handoff(parent)
    assert plan.read_bytes() == before
    assert (parent / ".wade/plans/handoff-progress.json").read_bytes() == progress
    raw = json.loads((parent / ".wade/plans/interactive-session.json").read_text())
    assert raw["session_id"] == identity and not raw["completed"]
    assert raw["review_required"] and not raw["refresh_pending"]
    assert list((parent / ".wade/plans").glob("session-refresh-*"))
    with pytest.raises(ValueError, match="Review is required"):
        interactive.complete(parent)
    interactive.record_review(plan, plan.read_text(), self_review=False)
    interactive.complete(parent)
    assert interactive.collect_with_state(parent)[0].session_id == identity


def test_interrupted_native_refresh_is_recoverable_without_claiming_completion(
    evidence_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.services import interactive_plan_service as interactive
    from wade.services import session_composition_service as composition

    parent, identity = _completed_parent(evidence_repo)
    original = composition.compose_session

    def interrupted(*args: object, **kwargs: object) -> None:
        raise OSError("simulated refresh interruption")

    monkeypatch.setattr(composition, "compose_session", interrupted)
    with pytest.raises(OSError, match="interruption"):
        interactive.refresh_handoff(parent)
    raw = json.loads((parent / ".wade/plans/interactive-session.json").read_text())
    assert raw["session_id"] == identity and raw["refresh_pending"] and not raw["completed"]
    with pytest.raises(ValueError, match="interrupted"):
        interactive.collect(parent)
    monkeypatch.setattr(composition, "compose_session", original)
    interactive.refresh_handoff(parent)
    with pytest.raises(ValueError, match="Review is required"):
        interactive.complete(parent)


def test_concurrent_handoff_delivery_is_idempotent_without_main_writes(owned_repo: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from wade.services.knowledge_proposals import proposals

    parent, identity = _completed_parent(owned_repo.parent / "main")
    main = owned_repo.parent / "main"
    before_main = (main / "KNOWLEDGE.ratings.jsonl").read_bytes()
    before_author = (owned_repo / "KNOWLEDGE.ratings.jsonl").read_bytes()
    with ThreadPoolExecutor(max_workers=4) as pool:
        events = list(
            pool.map(
                lambda _: knowledge.record_handoff_rating_for_session(
                    parent, KnowledgeConfig(enabled=True), "claim-a", "up", identity
                ),
                range(12),
            )
        )
    assert all(event == events[0] for event in events)
    assert len(proposals(owned_repo)) == 1
    assert (main / "KNOWLEDGE.ratings.jsonl").read_bytes() == before_main
    assert (owned_repo / "KNOWLEDGE.ratings.jsonl").read_bytes() == before_author


@pytest.mark.parametrize("from_main", [False, True])
def test_explicit_legacy_recovery_archives_owner_and_preserves_original_bytes(
    owned_repo: Path, from_main: bool
) -> None:
    from wade.services.knowledge_proposals import accept_proposal, recover_legacy

    main = owned_repo.parent / "main"
    parent, identity = _completed_parent(main)
    config = KnowledgeConfig(enabled=True)
    event = knowledge.create_rating_event("claim-a", "up").to_record() | {"handoff_id": identity}
    source = (
        main / "KNOWLEDGE.ratings.jsonl" if from_main else knowledge.staged_ratings_path(parent)
    )
    source.write_text(json.dumps(event) + "\n")
    if from_main:
        with source.open("a") as stream:
            stream.write(
                json.dumps(
                    knowledge.create_rating_event("claim-a", "down").to_record()
                    | {"handoff_id": "foreign-parent"}
                )
                + "\n"
            )
    before = source.read_bytes()
    recovered = recover_legacy(owned_repo, parent, config, from_main=from_main)
    assert len(recovered) == 1
    assert recovered[0].owner.session_id == identity
    assert recovered[0].original_payload == event
    assert source.read_bytes() == before
    assert recover_legacy(owned_repo, parent, config, from_main=from_main) == recovered
    accepted = accept_proposal(owned_repo, config, recovered[0].proposal_id)
    assert accepted.entry_id == "claim-a"
    if not from_main:
        assert knowledge.flush_staged_ratings(parent, main, config).success
        assert source.read_bytes() == before


def _collected_parent(main: Path) -> tuple[Path, ProjectConfig, str]:
    from crossby.models.ai import (
        AIToolID,
        PlanArtifactSource,
        PlanSessionBinding,
        PlanSessionResult,
    )

    from wade.models.plan_bundle import BUNDLE_MARKER, PlanBundle, PlanMember
    from wade.models.workflow import SessionKind
    from wade.services import native_plan_service as native
    from wade.services.plan_service import _ensure_handoff_progress, _save_handoff_binding
    from wade.services.session_composition_service import compose_session

    parent = main.parent / "collected-parent"
    git(main, "worktree", "add", "--detach", str(parent))
    config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True))
    compose_session(parent, main, config, kind=SessionKind.PLAN, task_id="510")
    bundle = PlanBundle(
        plans=(
            PlanMember(
                filename="PLAN.md",
                markdown=(
                    "# fix: collected owner\n\n## Complexity\neasy\n\n## Tasks\n- Check owner.\n"
                ),
            ),
        ),
        knowledge_votes=(),
    )
    result = PlanSessionResult(
        tool=AIToolID.CODEX,
        version="fixture",
        plan=BUNDLE_MARKER + "\n```json\n" + bundle.model_dump_json() + "\n```\n",
        session_id="collected-owner",
        native_mode="fixture",
        artifact_source=PlanArtifactSource.PROTOCOL_EVENT,
        binding=PlanSessionBinding.THREAD_TURN_IDS,
        thread_id="fixture-thread",
        turn_id="fixture-turn",
        artifact_id="fixture-artifact",
        exit_code=0,
    )
    native.save_artifact(parent, result)
    native.materialize(parent, bundle)
    _save_handoff_binding(parent, None, config)
    _ensure_handoff_progress(parent, result.session_id, None, config, ())
    return parent, config, result.session_id


def test_collected_parent_uses_the_same_verified_task_binding(owned_repo: Path) -> None:
    from wade.services.knowledge_proposals import accept_proposal, bind_completed_parent, proposals

    parent, config, identity = _collected_parent(owned_repo.parent / "main")
    knowledge.record_handoff_rating_for_session(parent, config.knowledge, "claim-a", "up", identity)
    bind_completed_parent(parent, identity)
    proposal = proposals(owned_repo)[0]
    assert accept_proposal(owned_repo, config.knowledge, proposal.proposal_id).entry_id == "claim-a"


def test_dirty_knowledge_arriving_during_push_preflight_clears_marker(
    owned_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    from wade.utils import markers

    done = importlib.import_module("wade.services.implementation_service.done")
    repo = owned_repo
    main = repo.parent / "main"
    config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True))
    original = done._gate_knowledge_valid

    def arrive(*args: object, **kwargs: object) -> bool:
        assert original(*args, **kwargs)
        with (repo / "KNOWLEDGE.ratings.jsonl").open("a") as stream:
            stream.write('{"id":"claim-a","dir":"up","event_id":"foreign-late"}\n')
        return True

    monkeypatch.setattr(done, "_gate_knowledge_valid", arrive)
    pushed: list[str] = []
    monkeypatch.setattr(
        done.git_repo, "push_branch", lambda *args, **kwargs: pushed.append("unsafe")
    )
    sha = git(repo, "rev-parse", "HEAD").strip()
    markers.write_marker(repo, "done", sha)
    assert not done._push_branch_with_recovery(main, "feat/510-owner", repo, repo, config)
    assert not pushed and not list((repo / ".wade").glob("done@*"))


def test_recovery_merge_reconciles_new_head_before_marker_or_push_retry(
    owned_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    from wade.git.repo import GitError

    done = importlib.import_module("wade.services.implementation_service.done")
    repo = owned_repo
    main = repo.parent / "main"
    remote = main.parent / "remote-candidate"
    git(main, "worktree", "add", "-b", "remote-candidate", str(remote))
    (remote / "KNOWLEDGE.ratings.jsonl").write_text("not valid JSON\n")
    git(remote, "commit", "-am", "test: corrupt remote knowledge history")
    remote_sha = git(remote, "rev-parse", "HEAD").strip()
    git(main, "update-ref", "refs/remotes/origin/feat/510-owner", remote_sha)
    pushes: list[str] = []

    def reject(*args: object, **kwargs: object) -> None:
        pushes.append(git(repo, "rev-parse", "HEAD").strip())
        raise GitError("non-fast-forward")

    monkeypatch.setattr(done.git_repo, "push_branch", reject)
    monkeypatch.setattr(done.git_sync, "fetch_origin", lambda *_: None)
    monkeypatch.setattr(done.prompts, "is_tty", lambda: True)
    monkeypatch.setattr(done.prompts, "select", lambda *_: 0)
    config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True))
    assert not done._push_branch_with_recovery(main, "feat/510-owner", repo, repo, config)
    assert len(pushes) == 1 and git(repo, "rev-parse", "HEAD").strip() == remote_sha
    assert not list((repo / ".wade").glob("done@*"))


@pytest.mark.parametrize("proof", ["test-only", "mixed", "external", "decision"])
def test_noncode_evidence_retains_precise_historical_meaning(owned_repo: Path, proof: str) -> None:
    from datetime import UTC, datetime

    from wade.models.knowledge import RelationshipEvent
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_reconcile import reconcile_knowledge
    from wade.services.knowledge_store import append_event

    repo = owned_repo
    head = git(repo, "rev-parse", "HEAD").strip()
    if proof in {"test-only", "mixed"}:
        supplied = [
            {
                "type": "test_run",
                "check": "unit answers",
                "command": "./scripts/test.sh",
                "checked_commit": head,
                "outcome": "passed",
            }
        ]
    elif proof == "external":
        supplied = [
            {
                "type": "external_url",
                "url": "https://example.invalid/captured",
                "fetched_at": datetime.now(UTC).isoformat(),
                "content_digest": "sha256:" + "a" * 64,
            }
        ]
    else:
        supplied = [{"type": "decision", "reference": "ADR-42"}]
    evidence = repo / ".wade/proof.json"
    evidence.write_text(json.dumps(supplied))
    event = check_entry(
        repo,
        KnowledgeConfig(enabled=True),
        "claim-a",
        "current",
        files=("source.py",) if proof == "mixed" else (),
        evidence_file=evidence,
        note="Explicit supplied proof",
    )
    git(repo, "add", "KNOWLEDGE.ratings.jsonl")
    git(repo, "commit", "-m", "docs: record explicitly typed proof")
    report = reconcile_knowledge(repo, KnowledgeConfig(enabled=True))
    assert report.entries[0].assessment == (
        "needs-recheck" if proof == "test-only" else "checked-current"
    )
    checks = report.entries[0].assessments[0].checks
    if proof in {"test-only", "mixed", "external"}:
        assert any(check.historical for check in checks)
    if proof == "external":
        assert "not fetched" in checks[0].reason
    if proof == "decision":
        append_event(
            repo / "KNOWLEDGE.ratings.jsonl",
            RelationshipEvent(event_id="retire", entry_id="claim-a", relation="retired").model_dump(
                mode="json"
            ),
        )
        git(repo, "commit", "-am", "docs: retire recorded decision")
        assert (
            reconcile_knowledge(repo, KnowledgeConfig(enabled=True)).entries[0].assessment
            == "needs-recheck"
        )
    assert event.evidence


@pytest.mark.parametrize("change", ["body", "tags", "rename", "delete", "rebase"])
def test_claim_metadata_and_git_history_are_distinct_from_evidence_drift(
    owned_repo: Path, change: str
) -> None:
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    config = KnowledgeConfig(enabled=True)
    check_entry(
        repo, config, "claim-a", "current", files=("source.py",), note="Original committed proof"
    )
    git(repo, "commit", "-am", "docs: record proof")
    if change == "body":
        (repo / "KNOWLEDGE.md").write_text(
            (repo / "KNOWLEDGE.md").read_text().replace("forty two", "seven")
        )
    elif change == "tags":
        knowledge.add_tag_to_entry(repo / "KNOWLEDGE.md", "claim-a", "testing")
    elif change == "rename":
        git(repo, "mv", "source.py", "renamed.py")
    elif change == "delete":
        git(repo, "rm", "source.py")
    else:
        main = repo.parent / "main"
        (main / "unrelated.md").write_text("Unrelated base advancement\n")
        git(main, "add", "unrelated.md")
        git(main, "commit", "-m", "docs: advance unrelated base")
        git(repo, "rebase", "main")
    if change != "rebase":
        git(repo, "commit", "-am", "test: change evidence context")
    report = reconcile_knowledge(repo, config)
    assert report.entries[0].assessment == (
        "checked-current" if change in {"tags", "rebase"} else "needs-recheck"
    )
    assert report.entries[0].assessments[0].event.evidence[0].type == "git_file"


def test_replaced_assessments_remain_visible_without_granting_authority(owned_repo: Path) -> None:
    from wade.services.knowledge_assessment import check_entry
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    config = KnowledgeConfig(enabled=True)
    old = check_entry(
        repo, config, "claim-a", "contradicted", files=("source.py",), note="Old observation"
    )
    new = check_entry(
        repo,
        config,
        "claim-a",
        "current",
        files=("source.py",),
        note="Resolved observation",
        replaces=(old.event_id,),
    )
    git(repo, "commit", "-am", "docs: retain replacement history")
    report = reconcile_knowledge(repo, config)
    assert report.entries[0].assessment == "checked-current"
    assert {r.event.event_id: r.active for r in report.entries[0].assessments} == {
        old.event_id: False,
        new.event_id: True,
    }
    view = knowledge.get_annotated_knowledge(repo, config, all_history=True)
    assert (
        view.content is not None
        and old.event_id in view.content
        and "replaced historical proof" in view.content
    )


def test_foreign_proposal_owner_is_a_read_only_reconciliation_error(
    owned_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.git.knowledge import common_directory
    from wade.services.knowledge_assessment import feedback
    from wade.services.knowledge_reconcile import reconcile_knowledge

    repo = owned_repo
    monkeypatch.setenv("WADE_KNOWLEDGE_OBSERVER", "1")
    feedback(repo, KnowledgeConfig(enabled=True), "claim-a", "useful")
    proposal_path = next((common_directory(repo) / ".wade/knowledge/proposals").glob("*/*.json"))
    payload = json.loads(proposal_path.read_text())
    payload["owner"]["repository_id"] = "sha256:" + "f" * 64
    proposal_path.write_text(json.dumps(payload))
    before = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "diff", "--cached"),
        git(repo, "status", "--porcelain"),
    )
    report = reconcile_knowledge(repo, KnowledgeConfig(enabled=True))
    assert not report.valid and any("ownership" in problem for problem in report.problems)
    assert before == (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "diff", "--cached"),
        git(repo, "status", "--porcelain"),
    )


def test_bounded_reviewer_observation_can_only_be_accepted_by_its_frozen_parent(
    owned_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
    from wade.services import delegation_service as delegation
    from wade.services.knowledge_assessment import feedback
    from wade.services.knowledge_ownership import actor
    from wade.services.knowledge_proposals import accept_proposal, proposals

    repo = owned_repo
    config = KnowledgeConfig(enabled=True)
    original_env = {
        key: os.environ.get(key)
        for key in ("WADE_KNOWLEDGE_OBSERVER", "WADE_KNOWLEDGE_OPERATION_ID")
    }

    def observe(request: DelegationRequest) -> DelegationResult:
        assert not actor(repo).author
        identity = feedback(repo, config, "claim-a", "useful", note="Bounded observation")
        assert isinstance(identity, str)
        assert not (repo / "KNOWLEDGE.ratings.jsonl").read_text()
        return DelegationResult(
            success=True, feedback="Fixture observation only", mode=request.mode
        )

    monkeypatch.setattr(delegation, "_dispatch", observe)
    result = delegation.delegate(
        DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Observe only",
            cwd=repo,
            invocation_id="fixture-reviewer-operation",
        )
    )
    assert result.success and actor(repo).author
    assert original_env == {key: os.environ.get(key) for key in original_env}
    proposal = proposals(repo)[0]
    assert proposal.owner.operation_id == "fixture-reviewer-operation"
    assert accept_proposal(repo, config, proposal.proposal_id).entry_id == "claim-a"


def test_primary_branch_in_linked_worktree_is_still_observation_only(evidence_repo: Path) -> None:
    from wade.models.workflow import SessionKind
    from wade.services.session_composition_service import compose_session

    main = evidence_repo
    git(main, "checkout", "--detach")
    root = main.parent / "linked-main"
    git(main, "worktree", "add", str(root), "main")
    compose_session(root, main, ProjectConfig(), kind=SessionKind.IMPLEMENTATION, task_id="510")
    with pytest.raises(ValueError, match="Tracked knowledge writes"):
        knowledge.append_knowledge(
            root, KnowledgeConfig(enabled=True), "unowned main write", "implementation"
        )


def test_outside_git_cannot_author_tracked_knowledge(tmp_path: Path) -> None:
    path = tmp_path / "KNOWLEDGE.md"
    path.write_text("Original prose\n")
    with pytest.raises(ValueError, match="not a git repository"):
        knowledge.append_knowledge(
            tmp_path, KnowledgeConfig(enabled=True), "unowned", "implementation"
        )
    assert path.read_text() == "Original prose\n"


def test_acceptance_retry_after_ack_failure_does_not_duplicate_receipt(
    owned_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.services import knowledge_proposals as queue

    repo = owned_repo
    parent, identity = _completed_parent(repo.parent / "main")
    config = KnowledgeConfig(enabled=True)
    knowledge.record_handoff_rating_for_session(parent, config, "claim-a", "up", identity)
    queue.bind_completed_parent(parent, identity)
    proposal = queue.proposals(repo)[0]
    original = queue.disposition

    def fail_ack(*args: object, **kwargs: object) -> None:
        raise OSError("simulated acknowledgment failure")

    monkeypatch.setattr(queue, "disposition", fail_ack)
    with pytest.raises(OSError, match="acknowledgment"):
        queue.accept_proposal(repo, config, proposal.proposal_id)
    before = (repo / "KNOWLEDGE.ratings.jsonl").read_bytes()
    monkeypatch.setattr(queue, "disposition", original)
    queue.accept_proposal(repo, config, proposal.proposal_id)
    assert (repo / "KNOWLEDGE.ratings.jsonl").read_bytes() == before
    assert len(queue.dispositions(repo)) == len(queue.proposals(repo)) == 1


def test_disabled_knowledge_does_not_add_observer_write_requirements(
    owned_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
    from wade.services import delegation_service as delegation
    from wade.services import knowledge_proposals as queue

    config_path = owned_repo / ".wade.yml"
    config_path.write_text(config_path.read_text().replace("enabled: true", "enabled: false"))

    def unexpected_write(*args: object, **kwargs: object) -> bool:
        raise AssertionError("Knowledge-disabled reviewers must not require queue writes")

    monkeypatch.setattr(queue, "atomic_write_state_file", unexpected_write)
    monkeypatch.setattr(
        delegation,
        "_dispatch",
        lambda request: DelegationResult(
            success=True, feedback="No observations", mode=request.mode
        ),
    )
    result = delegation.delegate(
        DelegationRequest(mode=DelegationMode.HEADLESS, prompt="Observe only", cwd=owned_repo)
    )
    assert result.success


@pytest.mark.parametrize("interrupted", [False, True])
def test_collected_contract_refresh_preserves_old_artifacts_and_requires_parent_review(
    evidence_repo: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    from unittest.mock import MagicMock

    from wade.models.workflow import SESSION_DEFINITIONS, SessionKind
    from wade.services import interactive_plan_service as interactive
    from wade.services import native_plan_service as native
    from wade.services import plan_service as recovery
    from wade.services import session_composition_service as composition

    original = SESSION_DEFINITIONS[SessionKind.PLAN]
    with monkeypatch.context() as old:
        old.setitem(
            SESSION_DEFINITIONS,
            SessionKind.PLAN,
            original.model_copy(update={"workflow_revision": 3}),
        )
        parent, config, identity = _collected_parent(evidence_repo)
    plans = parent / ".wade/plans"
    before = {path.name: path.read_bytes() for path in plans.iterdir() if path.is_file()}
    review = MagicMock(return_value=False)
    provider = MagicMock()
    get_provider = MagicMock(return_value=provider)
    monkeypatch.setattr(native, "review_materialized_plans", review)
    monkeypatch.setattr(recovery, "get_provider", get_provider)
    assert not recovery._recover_completed_handoff(
        recovery_root=parent, project_root=evidence_repo, config=config, yolo=True
    )
    review.assert_not_called()
    get_provider.assert_not_called()

    if interrupted:

        def fail_compose(*args: object, **kwargs: object) -> None:
            raise OSError("simulated collected refresh interruption")

        with monkeypatch.context() as failure:
            failure.setattr(composition, "compose_session", fail_compose)
            with pytest.raises(OSError, match="interruption"):
                interactive.refresh_handoff(parent)
        assert not recovery._recover_completed_handoff(
            recovery_root=parent, project_root=evidence_repo, config=config, yolo=True
        )
        review.assert_not_called()
        get_provider.assert_not_called()
    assert interactive.refresh_handoff(parent) == "collected"
    for name, content in before.items():
        assert (plans / name).read_bytes() == content
    assert not (plans / "interactive-session.json").exists()
    assert list(plans.glob("session-refresh-*"))
    manifest = composition.load_session_manifest_strict(parent)
    assert manifest.workflow_revision == 4
    assert native.load_artifact(parent)[0].session_id == identity
    assert not recovery._recover_completed_handoff(
        recovery_root=parent, project_root=evidence_repo, config=config, yolo=True
    )
    review.assert_called_once()
    provider.create_task.assert_not_called()
    provider.update_task.assert_not_called()


def test_collected_refresh_cannot_fall_back_past_unsafe_native_state(
    evidence_repo: Path, tmp_path: Path
) -> None:
    from wade.services import interactive_plan_service as interactive
    from wade.utils.safe_state import StateFileUnsafeError

    parent, _config, _identity = _collected_parent(evidence_repo)
    original = (parent / ".wade/session/manifest.json").read_bytes()
    outside = tmp_path / "outside-native-state.json"
    outside.write_text("untouched external state")
    (parent / ".wade/plans/interactive-session.json").symlink_to(outside)
    with pytest.raises(StateFileUnsafeError):
        interactive.refresh_handoff(parent)
    assert outside.read_text() == "untouched external state"
    assert (parent / ".wade/session/manifest.json").read_bytes() == original
