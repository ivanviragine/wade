"""Service-level authority for local knowledge writes and pending proposals."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import ParamSpec, TypeVar

from wade.git import knowledge as git_knowledge
from wade.git import repo as git_repo
from wade.models.config import ProjectConfig
from wade.models.knowledge import KnowledgeActor, KnowledgeAuthorBinding, KnowledgeOperationState
from wade.models.session_manifest import SessionManifest
from wade.utils.filelock import file_lock
from wade.utils.safe_state import atomic_write_state_file, read_state_file, state_file_present

OBSERVER_ENV = "WADE_KNOWLEDGE_OBSERVER"


_P = ParamSpec("_P")
_T = TypeVar("_T")


def knowledge_operation(function: Callable[_P, _T]) -> Callable[_P, _T]:
    """Expose expected local Git failures as user-facing service validation."""

    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        try:
            return function(*args, **kwargs)
        except git_repo.GitError as exc:
            raise ValueError(str(exc)) from exc

    return wrapped


def _manifest(root: Path) -> SessionManifest | None:
    raw = read_state_file(root, ("session",), "manifest.json")
    if raw is None:
        return None
    try:
        return SessionManifest.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError(
            "Knowledge ownership session manifest is invalid; explicitly refresh it"
        ) from exc


def author_association(
    root: Path,
    task_id: str,
    config: ProjectConfig | None = None,
    *,
    create: bool = False,
    enforce: bool = False,
    require_existing: bool = False,
) -> bool:
    """Validate immutable lifecycle-created ownership; actor reads never create it."""
    from wade.config.loader import load_config

    root = root.resolve()
    # Composition supplies its effective config before copied configuration
    # reaches this worktree. Ordinary actor reads use the actual local config.
    config = config if config is not None else load_config(root)
    if not config.knowledge.enabled:
        return False
    try:
        main = config.project.main_branch or git_repo.detect_main_branch(root)
        if (
            not git_repo.is_worktree(root)
            or not git_repo.is_head_attached(root)
            or git_repo.get_current_branch(root) == main
        ):
            return False
        expected = KnowledgeAuthorBinding(
            repository_id=git_knowledge.repository_identity(root),
            worktree_root=str(root),
            branch=git_repo.get_current_branch(root),
            provider=config.provider.name,
            task_id=task_id,
        )
    except git_repo.GitError:
        return False  # Template-only/non-Git sessions never obtain write authority.

    def existing() -> KnowledgeAuthorBinding | None:
        raw = read_state_file(root, (), "knowledge-author.json")
        if raw is None:
            if state_file_present(root, (), "knowledge-author.json"):
                raise ValueError("Knowledge author association is unsafe; preserve it for recovery")
            return None
        return KnowledgeAuthorBinding.model_validate_json(raw)

    prior = existing()
    if prior is not None:
        if prior != expected and (enforce or create):
            raise ValueError(
                "Knowledge worktree/branch/provider/task ownership differs from its original "
                "association; return to the owning worktree/branch rather than reassigning it"
            )
        return prior == expected
    if require_existing:
        raise ValueError(
            "Knowledge author association is missing; explicitly refresh the owning session first"
        )
    if not create:
        return False
    with file_lock(root / ".wade/knowledge-author.json", create_parent=False, resolve_path=False):
        prior = existing()
        if prior is not None:
            if prior != expected:
                raise ValueError("Conflicting original knowledge author association")
            return True
        if not atomic_write_state_file(
            root, (), "knowledge-author.json", expected.model_dump_json().encode()
        ):
            raise ValueError("Could not persist original knowledge author association")
    return True


@knowledge_operation
def actor(
    root: Path, *, create_operation: bool = False, operation_id: str | None = None
) -> KnowledgeActor:
    root = root.resolve()
    head = git_knowledge.resolve_commit(root, "HEAD")
    manifest = _manifest(root)
    session_id: str | None = None
    native = read_state_file(root, ("plans",), "interactive-session.json")
    if native is not None:
        try:
            session_id = json.loads(native).get("session_id")
        except (ValueError, AttributeError) as exc:
            raise ValueError("Native knowledge handoff identity is malformed") from exc
    elif read_state_file(root, ("plans",), "native-session.json") is not None:
        from wade.services.native_plan_service import load_artifact

        # Use only the public retained result after verifying its imported set.
        # Invalid native state never falls through to a manufactured operation.
        result, _ = load_artifact(root)
        session_id = result.session_id
    journal = root / ".wade/knowledge-operation.json"
    raw_journal = read_state_file(root, (), "knowledge-operation.json")
    saved = (
        KnowledgeOperationState.model_validate_json(raw_journal)
        if raw_journal is not None
        else None
    )
    delegated = os.environ.get("WADE_KNOWLEDGE_OPERATION_ID")
    identity = operation_id or delegated or session_id or (saved.operation_id if saved else None)
    if identity is None and create_operation:
        with file_lock(journal, create_parent=False, resolve_path=False):
            raw_journal = read_state_file(root, (), "knowledge-operation.json")
            saved = (
                KnowledgeOperationState.model_validate_json(raw_journal)
                if raw_journal is not None
                else None
            )
            if saved is None:
                identity = uuid.uuid4().hex
                saved = KnowledgeOperationState(
                    operation_id=identity, session_id=identity, base_commit=head
                )
                if not atomic_write_state_file(
                    root, (), "knowledge-operation.json", saved.model_dump_json().encode()
                ):
                    raise ValueError("Could not durably persist knowledge operation ownership")
            identity = saved.operation_id
    # A read alone never manufactures an operation or grants write authority.
    identity = identity or "unresolved"
    from wade.config.loader import load_config

    config = load_config(root)
    if manifest is not None:
        from wade.services.session_composition_service import validate_frozen_session_bundle

        try:
            validate_frozen_session_bundle(root, manifest)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
    main_branch = config.project.main_branch
    if main_branch is None:
        try:
            main_branch = git_repo.detect_main_branch(root)
        except git_repo.GitError:
            main_branch = None
    owns_task = (
        manifest is not None
        and manifest.session.value in {"implementation", "review-pr-comments"}
        and bool(manifest.task_id)
        and author_association(root, manifest.task_id or "")
    )
    author = (
        os.environ.get(OBSERVER_ENV) != "1"
        and manifest is not None
        and manifest.session.value in {"implementation", "review-pr-comments"}
        and bool(manifest.task_id)
        and git_repo.is_worktree(root)
        and git_repo.is_head_attached(root)
        and main_branch is not None
        and git_repo.get_current_branch(root) != main_branch
        and owns_task
    )
    return KnowledgeActor(
        root=str(root),
        repository_id=git_knowledge.repository_identity(root),
        operation_id=identity,
        session_id=delegated or session_id or (saved.session_id if saved else None),
        provider=config.provider.name,
        task_id=(
            manifest.task_id
            if manifest is not None and (manifest.session.value == "plan" or owns_task)
            else None
        ),
        session_kind=manifest.session.value if manifest else None,
        base_commit=head
        if operation_id or delegated or session_id
        else (saved.base_commit if saved else head),
        author=author,
    )


def assert_author(root: Path, *, implementation_only: bool = False) -> KnowledgeActor:
    context = actor(root)
    if not context.author or (implementation_only and context.session_kind != "implementation"):
        raise ValueError(
            "Tracked knowledge writes require the owning implementation worktree; "
            "main, detached sessions and reviewer delegations are observation-only. "
            "Retain a pending proposal instead."
        )
    return context


@knowledge_operation
def assert_author_path(path: Path) -> KnowledgeActor:
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    top = git_repo._run_git("rev-parse", "--show-toplevel", cwd=parent).stdout.strip()
    root = Path(top).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Tracked knowledge path escapes its owning worktree")
    return assert_author(root)
