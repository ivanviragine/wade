"""Pure snapshot reconciliation and explicit append-only duplicate normalization."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

import yaml

from wade.git import knowledge as git_knowledge
from wade.git.repo import GitError
from wade.models.config import KnowledgeConfig
from wade.models.knowledge import (
    AssessmentEvent,
    AssessmentReport,
    DecisionEvidence,
    DuplicateEvent,
    EntryReport,
    Evidence,
    EvidenceCheck,
    ExternalURLEvidence,
    GitFileEvidence,
    ReconcileReport,
    RelationshipEvent,
    TestRunEvidence,
)
from wade.services.knowledge_ownership import knowledge_operation
from wade.services.knowledge_store import append_event
from wade.utils.filelock import file_lock
from wade.utils.knowledge_events import (
    body_digest,
    canonical_json,
    decode_history,
    duplicate_digest,
    entry_kind_key,
    exact_duplicate_groups,
    legacy_yaml_history,
    live_assessments,
    utility_counts,
)
from wade.utils.knowledge_file import (
    parse_entries,
    resolve_knowledge_path,
    resolve_ratings_path,
    validate_knowledge_text,
)


def _history_text(root: Path, commit: str, path: str) -> str:
    text = git_knowledge.read_tree_text(root, commit, path)
    if text is not None:
        return text
    legacy = git_knowledge.read_tree_text(root, commit, str(Path(path).with_suffix(".yml")))
    if legacy is None:
        return ""
    return legacy_yaml_history(legacy)


def _evidence_check(
    root: Path, target: str, evidence: Evidence, *, mixed_code: bool, historical: bool
) -> EvidenceCheck:
    if isinstance(evidence, GitFileEvidence):
        try:
            blob = git_knowledge.tree_blob(root, target, evidence.path)
            actual = blob.object_id if blob is not None else None
            matches = actual == evidence.blob
            return EvidenceCheck(
                evidence=evidence,
                applicable=matches,
                expected=evidence.blob,
                actual=actual,
                reason="Source blob unchanged; checked commit is historical proof."
                if matches
                else "Referenced path is missing/renamed or its blob changed; recheck required.",
                historical=evidence.checked_commit != target,
            )
        except (GitError, ValueError) as exc:
            return EvidenceCheck(
                evidence=evidence, applicable=False, expected=evidence.blob, reason=str(exc)
            )
    if isinstance(evidence, TestRunEvidence):
        current = evidence.checked_commit == target
        return EvidenceCheck(
            evidence=evidence,
            applicable=current or mixed_code,
            expected=evidence.checked_commit,
            actual=target,
            reason="Recorded test result at this commit."
            if current
            else "Historical test result only; tests were not claimed to run on this tree.",
            historical=not current,
        )
    if isinstance(evidence, ExternalURLEvidence):
        return EvidenceCheck(
            evidence=evidence,
            applicable=True,
            expected=evidence.content_digest,
            reason="Supplied snapshot; remote bytes were not fetched or assumed current.",
            historical=True,
        )
    assert isinstance(evidence, DecisionEvidence)
    return EvidenceCheck(
        evidence=evidence,
        applicable=not historical,
        reason="Recorded decision reference; remains applicable until superseded/retired."
        if not historical
        else "Decision belongs to a superseded/retired entry.",
    )


@knowledge_operation
def reconcile_knowledge(
    root: Path,
    config: KnowledgeConfig,
    *,
    against: str = "HEAD",
    main_ref: str | None = "main",
    effective_base: str | None = None,
    apply: bool = False,
) -> ReconcileReport:
    """Resolve every tree once; never fetch or read source proof from a live checkout."""
    target = git_knowledge.resolve_commit(root, against)
    path = resolve_knowledge_path(root, config).relative_to(root.resolve()).as_posix()
    ratings_path = resolve_ratings_path(Path(path)).as_posix()
    text = git_knowledge.read_tree_text(root, target, path) or ""
    entries = parse_entries(text)
    history = decode_history(_history_text(root, target, ratings_path))
    problems = list(validate_knowledge_text(text)) + list(history.problems)
    from wade.services.knowledge_proposals import validate_queue_ownership

    try:
        validate_queue_ownership(root)
    except (ValueError, OSError, GitError) as exc:
        problems.append("Knowledge proposal ownership is invalid: " + str(exc))
    diagnostics: list[str] = []
    main: str | None = None
    main_entries: dict[str, str] = {}
    main_events: dict[str, str] = {}
    try:
        if main_ref is None:
            raise ValueError("No configured main branch")
        main = git_knowledge.resolve_commit(root, main_ref)
        main_text = git_knowledge.read_tree_text(root, main, path) or ""
        main_history = decode_history(_history_text(root, main, ratings_path))
        if validate_knowledge_text(main_text) or main_history.problems:
            diagnostics.append("Configured main knowledge is corrupt; publication unavailable.")
            main = None
        else:
            main_entries = {
                e.entry_id: duplicate_digest(e)
                for e in parse_entries(main_text)
                if e.entry_id is not None
            }
            main_events = {
                e.event_id: canonical_json(e.model_dump(mode="json")) for e in main_history.events
            }
    except (GitError, ValueError, yaml.YAMLError) as exc:
        diagnostics.append("Configured main unavailable; publication not guessed: " + str(exc))
    base: str | None = None
    changed: set[str] = set()
    base_entries: dict[str, str] = {}
    base_events: set[str] = set()
    if effective_base is not None or main is not None:
        base = git_knowledge.resolve_commit(root, effective_base) if effective_base else main
        assert base is not None
        changed = git_knowledge.changed_paths(root, base, target)
        base_entries = {
            e.entry_id: body_digest(e)
            for e in parse_entries(git_knowledge.read_tree_text(root, base, path) or "")
            if e.entry_id is not None
        }
        base_events = {
            e.event_id for e in decode_history(_history_text(root, base, ratings_path)).events
        }
    groups = exact_duplicate_groups(entries)
    representatives = {
        member: group.representative_id for group in groups for member in group.member_ids
    }
    known_ids = {e.entry_id for e in entries if e.entry_id is not None}
    for event in history.events:
        if event.entry_id not in known_ids:
            # Removed prose is historical, not a silent event transfer to another claim.
            diagnostics.append(
                f"Historical event {event.event_id} names absent entry {event.entry_id}."
            )
    reports: list[EntryReport] = []
    for entry in entries:
        digest = body_digest(entry)
        entry_id = entry.entry_id
        kind, key = entry_kind_key(entry)
        publication: Literal["integrated", "branch-local", "unavailable"] = (
            "unavailable"
            if main is None or entry_id is None
            else "integrated"
            if main_entries.get(entry_id) == duplicate_digest(entry)
            else "branch-local"
        )
        active = live_assessments(history, entry_id) if entry_id else ()
        relations = tuple(
            e for e in history.events if isinstance(e, RelationshipEvent) and e.entry_id == entry_id
        )
        historical_links = tuple(f"{e.relation}:{e.related_id or ''}" for e in relations)
        historical_links += tuple(
            "legacy superseded_by:" + str(r["superseded_by"])
            for r in history.legacy_records
            if r.get("id") == entry_id and r.get("superseded_by")
        )
        assessment_reports: list[AssessmentReport] = []
        reasons: list[str] = []
        for event in active:
            mixed = any(isinstance(e, GitFileEvidence) for e in event.evidence)
            checks = tuple(
                _evidence_check(
                    root, target, e, mixed_code=mixed, historical=bool(historical_links)
                )
                for e in event.evidence
            )
            applicable = event.body_digest == digest and all(check.applicable for check in checks)
            event_pub: Literal["integrated", "branch-local", "unavailable"] = (
                "unavailable"
                if main is None
                else "integrated"
                if main_events.get(event.event_id) == canonical_json(event.model_dump(mode="json"))
                else "branch-local"
            )
            assessment_reports.append(
                AssessmentReport(
                    event=event, publication=event_pub, applicable=applicable, checks=checks
                )
            )
            if event.body_digest != digest:
                reasons.append(f"Assessment {event.event_id} checked a different claim body.")
            reasons.extend(check.reason for check in checks if not check.applicable)
        signatures = {canonical_json([event.outcome, event.body_digest]) for event in active}
        state: Literal["unverified", "checked-current", "contradicted", "needs-recheck"]
        if not active:
            state = "unverified"
            reasons.append(
                "No committed-evidence assessment; legacy feedback grants no correctness authority."
            )
        elif len(signatures) > 1:
            state = "needs-recheck"
            reasons.append(
                "Concurrent incompatible live assessments; explicitly replace them to resolve."
            )
        elif not all(report.applicable for report in assessment_reports):
            state = "needs-recheck"
        else:
            state = "checked-current" if active[0].outcome == "current" else "contradicted"
        active_ids = {event.event_id for event in active}
        for prior in history.events:
            if (
                not isinstance(prior, AssessmentEvent)
                or prior.entry_id != entry_id
                or prior.event_id in active_ids
            ):
                continue
            mixed = any(isinstance(e, GitFileEvidence) for e in prior.evidence)
            checks = tuple(
                _evidence_check(
                    root, target, e, mixed_code=mixed, historical=bool(historical_links)
                )
                for e in prior.evidence
            )
            prior_pub: Literal["integrated", "branch-local", "unavailable"] = (
                "unavailable"
                if main is None
                else "integrated"
                if main_events.get(prior.event_id) == canonical_json(prior.model_dump(mode="json"))
                else "branch-local"
            )
            assessment_reports.append(
                AssessmentReport(
                    event=prior,
                    publication=prior_pub,
                    applicable=False,
                    active=False,
                    checks=checks,
                )
            )
        assessment_reports.sort(key=lambda item: item.event.event_id)
        up, down, stale = utility_counts(history, entry_id) if entry_id else (0, 0, 0)
        touched = bool(entry_id and base_entries.get(entry_id) != digest)
        touched |= any(
            e.event_id not in base_events for e in history.events if e.entry_id == entry_id
        )
        touched |= any(
            isinstance(e, GitFileEvidence) and e.path in changed
            for event in active
            for e in event.evidence
        )
        reports.append(
            EntryReport(
                entry_id=entry_id,
                body_digest=digest,
                kind=kind,
                claim_key=key,
                publication=publication,
                assessment=state,
                reasons=tuple(sorted(set(reasons))),
                assessments=tuple(assessment_reports),
                historical=historical_links,
                utility_useful=up,
                utility_not_useful=down,
                legacy_stale=stale,
                representative_id=representatives.get(entry_id) if entry_id else None,
                branch_touched=touched,
            )
        )
    dirty = git_knowledge.local_changes(
        root, (path, ratings_path, str(Path(ratings_path).with_suffix(".yml")))
    )
    report = ReconcileReport(
        checked_commit=target,
        main_commit=main,
        effective_base_commit=base,
        entries=tuple(reports),
        duplicate_groups=groups,
        problems=tuple(problems),
        diagnostics=tuple(diagnostics),
        local_dirty_paths=dirty,
        history=history,
    )
    if not apply:
        return report
    from wade.services.knowledge_ownership import assert_author

    assert_author(root)
    if target != git_knowledge.resolve_commit(root, "HEAD") or not report.valid:
        raise ValueError("Reconcile apply requires owned current HEAD and a valid committed report")
    expected = {}
    for group in groups:
        for member in group.member_ids:
            if member == group.representative_id:
                continue
            event = DuplicateEvent(
                event_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        canonical_json(
                            [
                                "duplicate-v1",
                                member,
                                group.representative_id,
                                group.duplicate_digest,
                            ]
                        ),
                    )
                ),
                entry_id=member,
                representative_id=group.representative_id,
                duplicate_digest=group.duplicate_digest,
            )
            expected[event.event_id] = event
    appended = 0
    local_knowledge = resolve_knowledge_path(root, config)
    local_ratings = resolve_ratings_path(local_knowledge)
    with file_lock(local_knowledge):
        if target != git_knowledge.resolve_commit(root, "HEAD"):
            raise ValueError("HEAD changed during normalization; recheck")
        if not local_knowledge.exists() or local_knowledge.read_text() != text:
            raise ValueError("Commit claim changes before normalization")
        # A repeated/concurrent apply may see only its own deterministic aliases.
        # Other working-copy feedback/assessments must first be committed.
        working_text = (
            local_ratings.read_text()
            if local_ratings.exists()
            else _history_text(root, target, ratings_path)
        )
        working = decode_history(working_text)
        committed_events = {e.event_id: e for e in history.events}
        working_events = {e.event_id: e for e in working.events}
        if working.problems or any(
            working_events.get(key) != event for key, event in committed_events.items()
        ):
            raise ValueError(
                "Uncommitted/conflicting knowledge history; commit before normalization"
            )
        if any(
            expected.get(key) != event
            for key, event in working_events.items()
            if key not in committed_events
        ):
            raise ValueError("Uncommitted non-normalization knowledge events; commit them first")

        def legacy_payloads(records: tuple[dict[str, object], ...]) -> list[str]:
            normalized = []
            for record in records:
                if record.get("seed") is True:
                    record = {
                        "id": record["id"],
                        "seed": True,
                        **{key: record.get(key, 0) for key in ("up", "down", "stale")},
                        "superseded_by": record.get("superseded_by"),
                    }
                normalized.append(canonical_json(record))
            return sorted(normalized)

        if legacy_payloads(working.legacy_records) != legacy_payloads(history.legacy_records):
            raise ValueError("Uncommitted legacy utility history; commit before normalization")
        legacy_path = local_ratings.with_suffix(".yml")
        committed_legacy = git_knowledge.read_tree_text(
            root, target, str(Path(ratings_path).with_suffix(".yml"))
        )
        if legacy_path.exists() and legacy_path.read_text() != committed_legacy:
            raise ValueError("Uncommitted legacy counters; commit before normalization")
        for event in expected.values():
            appended += append_event(local_ratings, event.model_dump(mode="json"))
    return report.model_copy(update={"normalization_events_appended": appended})


def render_report(report: ReconcileReport) -> str:
    """Stable proof-oriented text; JSON retains complete member/history details."""
    lines = [
        f"Knowledge tree {report.checked_commit}",
        f"Main publication tree: {report.main_commit or 'unavailable'}",
    ]
    for index, entry in enumerate(report.entries, 1):
        lines.append(
            f"{entry.entry_id or f'plain-entry-{index}'}: {entry.assessment}; {entry.publication}"
            + ("; branch-touched" if entry.branch_touched else "")
        )
        lines.extend("  " + reason for reason in entry.reasons)
        for assessment in entry.assessments:
            lines.append(
                f"  receipt {assessment.event.event_id}: {assessment.publication}; "
                f"{'active' if assessment.active else 'replaced historical proof'}"
            )
            for proof in assessment.checks:
                lines.append(
                    f"    {proof.evidence.type}: {proof.reason} "
                    f"expected={proof.expected} actual={proof.actual}"
                )
        if entry.representative_id:
            lines.append("  exact duplicate representative: " + entry.representative_id)
    lines.extend("ERROR: " + problem for problem in report.problems)
    lines.extend("NOTE: " + diagnostic for diagnostic in report.diagnostics)
    lines.extend("LOCAL UNCOMMITTED: " + path for path in report.local_dirty_paths)
    return "\n".join(lines) + "\n"
