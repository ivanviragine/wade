"""Ignored, owner-bound detached knowledge proposals; never a tracked main spool."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from wade.git import knowledge as git_knowledge
from wade.models.config import KnowledgeConfig
from wade.models.interactive_plan import PlanHandoffProgress
from wade.models.knowledge import (
    DetachedObserverOperationBinding,
    KnowledgeProposal,
    ObserverOperationBinding,
    ProposalAcceptanceEvent,
    ProposalDisposition,
    ProposalOwner,
    TaskOperationBinding,
)
from wade.models.plan_bundle import PlanBundle
from wade.models.session_manifest import SessionManifest
from wade.services import knowledge_ownership
from wade.services.knowledge_store import append_event
from wade.utils.filelock import file_lock
from wade.utils.knowledge_events import body_digest, canonical_json, decode_history, digest_text
from wade.utils.knowledge_file import parse_entries, resolve_knowledge_path, resolve_ratings_path
from wade.utils.safe_state import atomic_write_state_file, read_state_file


def _key(value: str) -> str:
    return digest_text(value).split(":", 1)[1]


def _location(root: Path, operation: str, proposal_id: str) -> tuple[Path, tuple[str, ...], str]:
    return (
        git_knowledge.common_directory(root),
        ("knowledge", "proposals", _key(operation)),
        _key(proposal_id) + ".json",
    )


@knowledge_ownership.knowledge_operation
def proposals(root: Path) -> tuple[KnowledgeProposal, ...]:
    common = git_knowledge.common_directory(root)
    directory = common / ".wade/knowledge/proposals"
    if directory.is_symlink():
        raise ValueError("Knowledge proposal queue cannot be a symlink")
    if not directory.exists():
        return ()
    result: list[KnowledgeProposal] = []
    for partition in sorted(directory.iterdir()):
        if partition.is_symlink() or not partition.is_dir():
            raise ValueError("Unsafe knowledge proposal partition")
        for path in sorted(partition.glob("*.json")):
            raw = read_state_file(common, ("knowledge", "proposals", partition.name), path.name)
            if raw is None:
                raise ValueError("Unreadable/unsafe knowledge proposal " + str(path))
            result.append(KnowledgeProposal.model_validate_json(raw))
    return tuple(sorted(result, key=lambda proposal: proposal.proposal_id))


def find_proposal(root: Path, proposal_id: str) -> KnowledgeProposal:
    matches = [p for p in proposals(root) if p.proposal_id == proposal_id]
    if len(matches) != 1:
        raise ValueError("Proposal ID is missing or ambiguous")
    return matches[0]


def _detached_observer(root: Path, operation_id: str) -> DetachedObserverOperationBinding | None:
    raw = read_state_file(
        git_knowledge.common_directory(root),
        ("knowledge", "detached-observers"),
        _key(operation_id) + ".json",
    )
    return DetachedObserverOperationBinding.model_validate_json(raw) if raw is not None else None


def stage_proposal(
    root: Path,
    config: KnowledgeConfig,
    entry_id: str,
    payload: dict[str, Any],
    *,
    operation_id: str | None = None,
    task_id: str | None = None,
    plan_filename: str | None = None,
) -> KnowledgeProposal:
    context = knowledge_ownership.actor(root, create_operation=True, operation_id=operation_id)
    observer = _detached_observer(root, context.operation_id)
    if observer is not None:
        plan_filename = plan_filename or observer.plan_filename
    path = resolve_knowledge_path(root, config)
    with file_lock(path):
        entry = next((e for e in parse_entries(path.read_text()) if e.entry_id == entry_id), None)
        if entry is None:
            raise ValueError("Proposal entry is missing from the local knowledge snapshot")
        digest = body_digest(entry)
    registered: bytes | None = None
    if observer is not None:
        owner = observer.owner
    else:
        registered = read_state_file(
            git_knowledge.common_directory(root),
            ("knowledge", "observer-bindings"),
            _key(context.operation_id) + ".json",
        )
        if registered is not None:
            frozen = ObserverOperationBinding.model_validate_json(registered).task
            owner = ProposalOwner(
                repository_id=frozen.repository_id,
                operation_id=frozen.operation_id,
                session_id=frozen.session_id,
                provider=frozen.provider,
                task_id=frozen.originating_task_id,
                base_commit=frozen.base_commit,
            )
        else:
            owner = ProposalOwner(
                repository_id=context.repository_id,
                operation_id=context.operation_id,
                session_id=operation_id or context.session_id,
                provider=context.provider,
                task_id=task_id or context.task_id,
                base_commit=context.base_commit,
            )
    if (
        owner.repository_id != context.repository_id
        or owner.operation_id != context.operation_id
        or owner.provider != context.provider
        or (task_id is not None and owner.task_id != task_id)
    ):
        raise ValueError("Observer differs from its registered repository/task ownership")
    # The registration, not a concurrently advanced live HEAD, owns the
    # observation snapshot. A changed claim requires a fresh bounded operation.
    original_text = git_knowledge.read_tree_text(
        root, owner.base_commit, path.relative_to(root.resolve()).as_posix()
    )
    original_entry = next(
        (e for e in parse_entries(original_text or "") if e.entry_id == entry_id), None
    )
    if (observer is not None or registered is not None) and (
        original_entry is None or body_digest(original_entry) != digest
    ):
        raise ValueError("Claim differs from the observer's original frozen snapshot; retry fresh")
    stable_payload = dict(payload)
    if stable_payload.get("event_id"):
        stable_payload.pop("ts", None)
    proposal_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            canonical_json(
                [
                    "wade-knowledge-proposal-v1",
                    owner.model_dump(mode="json"),
                    entry_id,
                    digest,
                    stable_payload,
                    plan_filename,
                ]
            ),
        )
    )
    common, directories, filename = _location(root, owner.operation_id, proposal_id)
    lock = common / ".wade/knowledge/queue.lock"
    with file_lock(lock, create_parent=False, resolve_path=False):
        previous = read_state_file(common, directories, filename)
        if previous is not None:
            proposal = KnowledgeProposal.model_validate_json(previous)
            if proposal.owner != owner or proposal.body_digest != digest:
                raise ValueError("Conflicting durable proposal identity")
            return proposal
        proposal = KnowledgeProposal(
            proposal_id=proposal_id,
            owner=owner,
            entry_id=entry_id,
            body_digest=digest,
            original_payload=payload,
            proposed_at=datetime.now(UTC),
            plan_filename=plan_filename,
        )
        if not atomic_write_state_file(
            common, directories, filename, proposal.model_dump_json().encode()
        ):
            raise ValueError("Could not durably persist knowledge proposal")
        return proposal


def _parent_snapshot(root: Path) -> tuple[str, PlanBundle, PlanHandoffProgress, SessionManifest]:
    """Read and revalidate original native or collected parent ownership proof."""
    from wade.models.workflow import SessionKind
    from wade.services import interactive_plan_service as interactive
    from wade.services import native_plan_service as native
    from wade.services.session_composition_service import (
        load_session_manifest_strict,
        validate_frozen_session_bundle,
    )

    manifest = load_session_manifest_strict(root)
    validate_frozen_session_bundle(root, manifest, expected_kind=SessionKind.PLAN)
    if interactive.active(root):
        state, bundle = interactive.collect_with_state(root)
        session_id = state.session_id
    else:
        result, bundle = native.load_artifact(root)
        session_id = result.session_id
    progress_raw = read_state_file(root, ("plans",), "handoff-progress.json")
    if progress_raw is None:
        raise ValueError("Original completed parent task progress is required")
    progress = PlanHandoffProgress.model_validate_json(progress_raw)
    if progress.session_id != session_id:
        raise ValueError("Proposal association does not match completed parent session")
    return session_id, bundle, progress, manifest


def bind_task_operation(root: Path, binding: TaskOperationBinding, *, proposal_id: str) -> None:
    """Persist one proposal's task association from the original completed parent.

    A task number supplied in a consumer worktree is never ownership proof.
    Multi-plan observations without a named member remain unresolved.
    """
    if binding.repository_id != git_knowledge.repository_identity(root):
        raise ValueError("Task binding belongs to a different repository")
    session_id, bundle, progress, manifest = _parent_snapshot(root)
    observer = _detached_observer(root, binding.operation_id)
    parent_id = observer.parent_session_id if observer is not None else binding.session_id
    if session_id != parent_id:
        raise ValueError("Proposal association does not match completed parent session")
    if progress.provider is None or progress.provider.name != binding.provider:
        raise ValueError("Proposal task provider differs from original parent")
    mapping = dict(progress.persisted_issues)
    if len(bundle.plans) == 1 and manifest.task_id is not None:
        mapping.setdefault(bundle.plans[0].filename, manifest.task_id)
    if mapping.get(binding.plan_filename or "") != binding.task_id:
        raise ValueError("No actual persisted task/member association for this proposal")
    proposal = find_proposal(root, proposal_id)
    if observer is not None and (
        observer.owner != proposal.owner
        or observer.plan_filename not in {None, binding.plan_filename}
    ):
        raise ValueError("Proposal differs from its original detached parent operation")
    if (
        proposal.owner.operation_id != binding.operation_id
        or proposal.owner.session_id != binding.session_id
    ):
        raise ValueError("Proposal belongs to another operation/session")
    if proposal.plan_filename is None and len(bundle.plans) != 1:
        raise ValueError("Multi-plan proposal lacks a member owner; retain it unresolved")
    if proposal.plan_filename is not None and proposal.plan_filename != binding.plan_filename:
        raise ValueError("Proposal names another plan member")
    if proposal.owner.task_id is not None and proposal.owner.task_id != manifest.task_id:
        raise ValueError("Proposal task differs from persisted owner")
    if proposal.owner.base_commit != binding.base_commit:
        raise ValueError("Proposal base snapshot differs from original owner")
    common = git_knowledge.common_directory(root)
    directories = ("knowledge", "bindings", _key(binding.operation_id))
    filename = "proposal-" + _key(proposal_id) + ".json"
    with file_lock(common / ".wade/knowledge/queue.lock", create_parent=False, resolve_path=False):
        prior = read_state_file(common, directories, filename)
        if prior is not None and TaskOperationBinding.model_validate_json(prior) != binding:
            raise ValueError("Conflicting persisted proposal/task association")
        if prior is None and not atomic_write_state_file(
            common, directories, filename, binding.model_dump_json().encode()
        ):
            raise ValueError("Could not durably bind proposal task ownership")


def bind_completed_parent(root: Path, session_id: str) -> None:
    """Called only after task persistence, before offers/cleanup of the parent."""
    pending = [
        p
        for p in proposals(root)
        if (p.owner.session_id == session_id and p.owner.operation_id == session_id)
        or (
            (observer := _detached_observer(root, p.owner.operation_id)) is not None
            and observer.parent_session_id == session_id
        )
    ]
    if not pending:
        return
    actual_id, bundle, progress, manifest = _parent_snapshot(root)
    if actual_id != session_id or progress.provider is None:
        raise ValueError("Completed proposal parent identity is unavailable")
    mapping = dict(progress.persisted_issues)
    if len(bundle.plans) == 1 and manifest.task_id is not None:
        mapping.setdefault(bundle.plans[0].filename, manifest.task_id)
    for proposal in pending:
        filename = proposal.plan_filename
        if filename is None and len(bundle.plans) == 1:
            filename = bundle.plans[0].filename
        if filename is None or filename not in mapping:
            continue
        bind_task_operation(
            root,
            TaskOperationBinding(
                repository_id=proposal.owner.repository_id,
                operation_id=proposal.owner.operation_id,
                session_id=proposal.owner.session_id or session_id,
                provider=progress.provider.name,
                task_id=mapping[filename],
                originating_task_id=manifest.task_id,
                base_commit=proposal.owner.base_commit,
                plan_filename=filename,
            ),
            proposal_id=proposal.proposal_id,
        )


def _binding(
    root: Path, proposal: KnowledgeProposal, provider: str, task_id: str
) -> TaskOperationBinding:
    common = git_knowledge.common_directory(root)
    raw = read_state_file(
        common,
        ("knowledge", "bindings", _key(proposal.owner.operation_id)),
        "proposal-" + _key(proposal.proposal_id) + ".json",
    )
    if raw is None:
        from wade.models.knowledge import ObserverOperationBinding

        observer = read_state_file(
            common, ("knowledge", "observer-bindings"), _key(proposal.owner.operation_id) + ".json"
        )
        if observer is not None:
            raw = (
                ObserverOperationBinding.model_validate_json(observer)
                .task.model_dump_json()
                .encode()
            )
    if raw is None:
        detached = _detached_observer(root, proposal.owner.operation_id)
        if (
            detached is not None
            and detached.owner == proposal.owner
            and detached.parent_session_id is None
            and len(detached.task_ids) == 1
        ):
            raw = (
                TaskOperationBinding(
                    repository_id=proposal.owner.repository_id,
                    operation_id=proposal.owner.operation_id,
                    session_id=proposal.owner.session_id or proposal.owner.operation_id,
                    provider=proposal.owner.provider or "",
                    task_id=detached.task_ids[0],
                    originating_task_id=proposal.owner.task_id,
                    base_commit=proposal.owner.base_commit,
                )
                .model_dump_json()
                .encode()
            )
    if raw is None:
        raise ValueError(
            "Proposal has no verified parent task/operation association; retain it pending"
        )
    binding = TaskOperationBinding.model_validate_json(raw)
    if (
        binding.repository_id != proposal.owner.repository_id
        or binding.operation_id != proposal.owner.operation_id
        or binding.session_id != proposal.owner.session_id
        or binding.provider != provider
        or binding.task_id != task_id
        or binding.base_commit != proposal.owner.base_commit
        or binding.originating_task_id != proposal.owner.task_id
        or (proposal.plan_filename is not None and binding.plan_filename != proposal.plan_filename)
    ):
        raise ValueError("Proposal ownership does not match its persisted parent binding")
    return binding


@knowledge_ownership.knowledge_operation
def dispositions(root: Path) -> tuple[ProposalDisposition, ...]:
    path = git_knowledge.common_directory(root) / ".wade/knowledge/dispositions.jsonl"
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Unsafe proposal dispositions")
    if not path.exists():
        return ()
    return tuple(
        ProposalDisposition.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    )


def disposition(
    root: Path,
    proposal_id: str,
    action: Literal["retained", "discarded", "accepted", "assessed"],
    note: str,
    event_id: str | None = None,
) -> ProposalDisposition:
    find_proposal(root, proposal_id)
    identity = str(
        uuid.uuid5(uuid.NAMESPACE_URL, canonical_json([proposal_id, action, event_id, note]))
    )
    path = git_knowledge.common_directory(root) / ".wade/knowledge/dispositions.jsonl"
    existing = next((d for d in dispositions(root) if d.disposition_id == identity), None)
    if existing is not None:
        return existing
    item = ProposalDisposition(
        proposal_id=proposal_id,
        disposition_id=identity,
        action=action,
        at=datetime.now(UTC),
        note=note,
        event_id=event_id,
    )
    append_event(path, item.model_dump(mode="json"), tracked=False)
    return item


def verify_acceptance(
    root: Path, config: KnowledgeConfig, proposal_id: str, entry_id: str | None = None
) -> KnowledgeProposal:
    actor = knowledge_ownership.assert_author(root, implementation_only=True)
    proposal = find_proposal(root, proposal_id)
    if (
        actor.repository_id != proposal.owner.repository_id
        or actor.provider != proposal.owner.provider
        or actor.provider is None
        or actor.task_id is None
    ):
        raise ValueError("Proposal belongs to another repository/provider or unresolved owner")
    _binding(root, proposal, actor.provider, actor.task_id)
    if entry_id is not None and proposal.entry_id != entry_id:
        raise ValueError("Proposal assessment names another knowledge entry")
    if any(d.proposal_id == proposal_id and d.action == "discarded" for d in dispositions(root)):
        raise ValueError("Proposal was explicitly discarded")
    entry = next(
        (
            e
            for e in parse_entries(resolve_knowledge_path(root, config).read_text())
            if e.entry_id == proposal.entry_id
        ),
        None,
    )
    if entry is None or body_digest(entry) != proposal.body_digest:
        raise ValueError(
            "Proposal claim content changed; retain pending and recheck current content"
        )
    return proposal


def accept_proposal(
    root: Path, config: KnowledgeConfig, proposal_id: str
) -> ProposalAcceptanceEvent:
    proposal = verify_acceptance(root, config, proposal_id)
    path = resolve_knowledge_path(root, config)
    ratings = resolve_ratings_path(path)
    with file_lock(path):
        proposal = verify_acceptance(root, config, proposal_id)
        history = decode_history(ratings.read_text() if ratings.exists() else "")
        if history.problems:
            raise ValueError("; ".join(history.problems))
        existing = next(
            (
                e
                for e in history.events
                if isinstance(e, ProposalAcceptanceEvent) and e.proposal_id == proposal_id
            ),
            None,
        )
        common = git_knowledge.common_directory(root)
        directories = ("knowledge", "acceptances")
        filename = _key(proposal_id) + ".json"
        with file_lock(
            common / ".wade/knowledge/queue.lock", create_parent=False, resolve_path=False
        ):
            raw = read_state_file(common, directories, filename)
            event = (
                ProposalAcceptanceEvent.model_validate_json(raw)
                if raw is not None
                else existing
                or ProposalAcceptanceEvent(
                    event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "knowledge-accept:" + proposal_id)),
                    entry_id=proposal.entry_id,
                    proposal_id=proposal_id,
                    body_digest=proposal.body_digest,
                    accepted_at=datetime.now(UTC),
                )
            )
            if (
                event.entry_id != proposal.entry_id
                or event.proposal_id != proposal_id
                or event.body_digest != proposal.body_digest
                or event.event_id
                != str(uuid.uuid5(uuid.NAMESPACE_URL, "knowledge-accept:" + proposal_id))
                or (existing is not None and existing != event)
            ):
                raise ValueError("Conflicting immutable proposal acceptance; originals retained")
            if raw is None and not atomic_write_state_file(
                common, directories, filename, event.model_dump_json().encode()
            ):
                raise ValueError("Could not persist immutable shared acceptance before append")
            if existing is None:
                append_event(ratings, event.model_dump(mode="json"))
            disposition(
                root,
                proposal_id,
                "accepted",
                "Owner/content verified; no correctness assessment granted. "
                "Fresh committed-evidence check required.",
                event.event_id,
            )
        return event


@knowledge_ownership.knowledge_operation
def recover_legacy(
    root: Path, source: Path, config: KnowledgeConfig, *, from_main: bool = False
) -> tuple[KnowledgeProposal, ...]:
    """Explicitly archive an original retained parent's legacy log into its queue.

    Ownership comes from the retained parent, never from the next consumer or
    a caller-supplied task number. Original transport/main bytes stay untouched.
    """
    import json

    from wade.services.knowledge_service import _load_staged_rating_records, staged_ratings_path

    context = knowledge_ownership.actor(source)
    if context.repository_id != git_knowledge.repository_identity(root):
        raise ValueError("Recovery source belongs to another repository")
    if context.session_id is None or context.operation_id == "unresolved":
        raise ValueError("Original retained parent identity is unavailable; preserve legacy bytes")
    if from_main:
        from collections import Counter

        from wade.git import repo as git_repo

        main = git_repo.main_checkout_root(root)
        path = resolve_ratings_path(resolve_knowledge_path(main, config))
    else:
        path = staged_ratings_path(source)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Unsafe legacy transport path; original retained")
    result = []
    with file_lock(path, create_parent=False):
        original_text = path.read_text()
        if from_main:
            committed = (
                git_knowledge.read_tree_text(
                    main,
                    git_knowledge.resolve_commit(main, "HEAD"),
                    path.relative_to(main).as_posix(),
                )
                or ""
            )
            baseline = Counter(committed.splitlines())
            working = Counter(original_text.splitlines())
            if baseline - working:
                raise ValueError(
                    "Main legacy spool includes rewrites; preserve it without guessing a delta"
                )
            pending = working - baseline
            records = [
                json.loads(line)
                for line in original_text.splitlines()
                if line.strip() and pending.get(line, 0) > 0
            ]
            records = [
                record
                for record in records
                if isinstance(record, dict) and record.get("handoff_id") == context.session_id
            ]
            if not records:
                raise ValueError(
                    "No pending main feedback has this original parent's explicit handoff identity"
                )
        else:
            records = _load_staged_rating_records(path)
        for record in records:
            if record.get("handoff_id", context.session_id) != context.session_id:
                raise ValueError("Legacy event names another handoff; original retained")
            if (
                not isinstance(record.get("id"), str)
                or record.get("dir") not in {"up", "down", "stale"}
                or not isinstance(record.get("event_id"), str)
            ):
                raise ValueError("Malformed legacy pending event; original retained")
            result.append(
                stage_proposal(
                    source, config, str(record["id"]), record, operation_id=context.session_id
                )
            )
    # Persist complete original bytes as a separate recovery artifact as well.
    common = git_knowledge.common_directory(root)
    payload = json.dumps(
        {
            "source": str(path),
            "session_id": context.session_id,
            "original_text": original_text,
            "proposal_ids": [p.proposal_id for p in result],
        }
    )
    if not atomic_write_state_file(
        common,
        ("knowledge", "legacy-recovery"),
        _key(str(path) + payload) + ".json",
        payload.encode(),
    ):
        raise ValueError("Could not durably archive original legacy log; original retained")
    # Association is verified from the retained completed parent, never supplied
    # by the recovering consumer. Unfinished/dependency observations stay pending.
    native = read_state_file(source, ("plans",), "interactive-session.json")
    if native is not None:
        from wade.models.interactive_plan import InteractivePlanState

        if InteractivePlanState.model_validate_json(native).completed:
            bind_completed_parent(source, context.session_id)
    elif read_state_file(source, ("plans",), "native-session.json") is not None:
        # Collected results have no interactive completion flag. Their validated
        # artifact plus actual persisted progress is the same ownership proof.
        bind_completed_parent(source, context.session_id)
    return tuple(result)


@knowledge_ownership.knowledge_operation
def legacy_transport_archived(root: Path, source: Path) -> bool:
    """Only an explicit recovery's exact durable archive permits legacy cleanup."""
    import json

    from wade.services.knowledge_service import staged_ratings_path

    path = staged_ratings_path(source)
    original = path.read_text()
    common = git_knowledge.common_directory(root)
    directory = common / ".wade/knowledge/legacy-recovery"
    if directory.is_symlink() or not directory.exists():
        return False
    known = {p.proposal_id for p in proposals(root)}
    for file in directory.glob("*.json"):
        raw = read_state_file(common, ("knowledge", "legacy-recovery"), file.name)
        if raw is None:
            continue
        record = json.loads(raw)
        if record.get("source") == str(path) and record.get("original_text") == original:
            return set(record.get("proposal_ids", ())) <= known
    return False


def validate_queue_ownership(root: Path) -> None:
    """Validate ignored queue structure/ownership without accepting any proposal."""
    common = git_knowledge.common_directory(root)
    identity = git_knowledge.repository_identity(root)
    pending = {p.proposal_id: p for p in proposals(root)}
    if any(p.owner.repository_id != identity for p in pending.values()):
        raise ValueError("Knowledge proposal queue contains a foreign repository owner")
    for record in dispositions(root):
        if record.proposal_id not in pending:
            raise ValueError("Knowledge disposition names an unavailable original proposal")
    acceptance_directory = common / ".wade/knowledge/acceptances"
    if acceptance_directory.is_symlink():
        raise ValueError("Unsafe immutable acceptance directory")
    if acceptance_directory.exists():
        for path in acceptance_directory.glob("*.json"):
            raw = read_state_file(common, ("knowledge", "acceptances"), path.name)
            if raw is None:
                raise ValueError("Unreadable immutable acceptance")
            accepted = ProposalAcceptanceEvent.model_validate_json(raw)
            proposal = pending.get(accepted.proposal_id)
            if (
                proposal is None
                or path.name != _key(accepted.proposal_id) + ".json"
                or accepted.entry_id != proposal.entry_id
                or accepted.body_digest != proposal.body_digest
                or accepted.event_id
                != str(uuid.uuid5(uuid.NAMESPACE_URL, "knowledge-accept:" + accepted.proposal_id))
            ):
                raise ValueError("Immutable acceptance differs from original proposal")
    detached_directory = common / ".wade/knowledge/detached-observers"
    if detached_directory.is_symlink():
        raise ValueError("Unsafe detached observer ownership directory")
    if detached_directory.exists():
        for path in detached_directory.glob("*.json"):
            raw = read_state_file(common, ("knowledge", "detached-observers"), path.name)
            if raw is None:
                raise ValueError("Unreadable detached observer ownership")
            detached = DetachedObserverOperationBinding.model_validate_json(raw)
            if (
                detached.owner.repository_id != identity
                or path.name != _key(detached.owner.operation_id) + ".json"
            ):
                raise ValueError("Foreign detached observer ownership")
            for proposal in pending.values():
                if (
                    proposal.owner.operation_id == detached.owner.operation_id
                    and proposal.owner != detached.owner
                ):
                    raise ValueError("Detached proposal differs from original parent ownership")
    observer_directory = common / ".wade/knowledge/observer-bindings"
    if observer_directory.is_symlink():
        raise ValueError("Unsafe bounded observer ownership directory")
    if observer_directory.exists():
        from wade.models.knowledge import ObserverOperationBinding

        for path in observer_directory.glob("*.json"):
            raw = read_state_file(common, ("knowledge", "observer-bindings"), path.name)
            if raw is None:
                raise ValueError("Unreadable bounded observer ownership")
            observer = ObserverOperationBinding.model_validate_json(raw)
            if (
                observer.task.repository_id != identity
                or path.name != _key(observer.task.operation_id) + ".json"
            ):
                raise ValueError("Foreign bounded observer task ownership")
            for proposal in pending.values():
                if proposal.owner.operation_id == observer.task.operation_id:
                    owner = proposal.owner
                    if (
                        owner.session_id != observer.task.session_id
                        or owner.provider != observer.task.provider
                        or owner.task_id != observer.task.task_id
                        or owner.base_commit != observer.task.base_commit
                    ):
                        raise ValueError(
                            "Observer proposal differs from its original frozen parent task"
                        )
    directory = common / ".wade/knowledge/bindings"
    if directory.is_symlink():
        raise ValueError("Unsafe knowledge ownership bindings")
    if not directory.exists():
        return
    for partition in directory.iterdir():
        if partition.is_symlink() or not partition.is_dir():
            raise ValueError("Unsafe knowledge ownership partition")
        for path in partition.glob("*.json"):
            raw = read_state_file(common, ("knowledge", "bindings", partition.name), path.name)
            if raw is None:
                raise ValueError("Unreadable knowledge task ownership binding")
            binding = TaskOperationBinding.model_validate_json(raw)
            owners = [
                p
                for p in pending.values()
                if "proposal-" + _key(p.proposal_id) + ".json" == path.name
            ]
            if len(owners) != 1:
                raise ValueError("Knowledge task binding lacks its original proposal")
            proposal = owners[0]
            if (
                binding.repository_id != identity
                or binding.operation_id != proposal.owner.operation_id
                or binding.session_id != proposal.owner.session_id
                or binding.provider != proposal.owner.provider
                or binding.originating_task_id != proposal.owner.task_id
                or binding.base_commit != proposal.owner.base_commit
                or partition.name != _key(binding.operation_id)
            ):
                raise ValueError(
                    "Knowledge task binding conflicts with original proposal ownership"
                )


def register_observer_operation(
    root: Path,
    operation_id: str,
    *,
    plan_filename: str | None = None,
    task_ids: tuple[str, ...] = (),
    delegation_digest: str | None = None,
) -> None:
    """Bind an existing owning task before launch; do not accept any feedback."""
    from wade.config.loader import load_config
    from wade.models.knowledge import ObserverOperationBinding
    from wade.models.workflow import SessionKind
    from wade.services.session_composition_service import load_session_manifest

    if not load_config(root).knowledge.enabled:
        return

    manifest = load_session_manifest(root)
    from wade.git import repo as git_repo

    detached_deps = manifest is None and delegation_digest is not None
    if manifest is None and not detached_deps:
        return
    if detached_deps and (not git_repo.is_worktree(root) or git_repo.is_head_attached(root)):
        raise ValueError("Standalone dependency ownership requires its detached worktree")
    context = knowledge_ownership.actor(root)
    if detached_deps or (manifest is not None and manifest.session is SessionKind.PLAN):
        detached_binding = DetachedObserverOperationBinding(
            owner=ProposalOwner(
                repository_id=context.repository_id,
                operation_id=operation_id,
                session_id=operation_id,
                provider=context.provider,
                task_id=context.task_id,
                base_commit=context.base_commit,
            ),
            frozen_bundle_digest=manifest.bundle_digest
            if manifest is not None
            else delegation_digest or "",
            parent_worktree=str(root.resolve()),
            parent_session_id=context.session_id if manifest is not None else None,
            plan_filename=plan_filename,
            task_ids=task_ids if detached_deps else (),
        )
        if manifest is not None and detached_binding.parent_session_id is None:
            return  # A parent without a stable handoff identity remains unresolved.
        common = git_knowledge.common_directory(root)
        parts = ("knowledge", "detached-observers")
        filename = _key(operation_id) + ".json"
        with file_lock(
            common / ".wade/knowledge/queue.lock", create_parent=False, resolve_path=False
        ):
            previous = read_state_file(common, parts, filename)
            if (
                previous is not None
                and DetachedObserverOperationBinding.model_validate_json(previous)
                != detached_binding
            ):
                raise ValueError("Conflicting detached observer operation identity")
            if previous is None and not atomic_write_state_file(
                common, parts, filename, detached_binding.model_dump_json().encode()
            ):
                raise ValueError("Could not durably bind detached observer parent")
        return
    if manifest is None or manifest.session not in {
        SessionKind.IMPLEMENTATION,
        SessionKind.REVIEW_PR_COMMENTS,
    }:
        return
    if not context.author or context.task_id is None or context.provider is None:
        return
    binding = ObserverOperationBinding(
        task=TaskOperationBinding(
            repository_id=context.repository_id,
            operation_id=operation_id,
            session_id=operation_id,
            provider=context.provider,
            task_id=context.task_id,
            originating_task_id=context.task_id,
            base_commit=git_knowledge.resolve_commit(root, "HEAD"),
        ),
        frozen_bundle_digest=manifest.bundle_digest,
        parent_worktree=str(root.resolve()),
    )
    common = git_knowledge.common_directory(root)
    parts = ("knowledge", "observer-bindings")
    filename = _key(operation_id) + ".json"
    with file_lock(common / ".wade/knowledge/queue.lock", create_parent=False, resolve_path=False):
        previous = read_state_file(common, parts, filename)
        if (
            previous is not None
            and ObserverOperationBinding.model_validate_json(previous) != binding
        ):
            raise ValueError("Conflicting bounded observer operation identity")
        if previous is None and not atomic_write_state_file(
            common, parts, filename, binding.model_dump_json().encode()
        ):
            raise ValueError("Could not durably bind reviewer observations to their owning task")
