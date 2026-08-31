"""Active dynamic-session state diagnostics and refresh commands."""

from __future__ import annotations

import os
from pathlib import Path

import typer

session_app = typer.Typer(help="Inspect or refresh the active WADE session bundle.")


def _fallback_root() -> Path | None:
    """Return the worktree-less planning bundle root advertised by the parent."""

    from wade.models.readiness import PLAN_DIR_ENV_VAR

    raw = os.environ.get(PLAN_DIR_ENV_VAR)
    return Path(raw).absolute() if raw else None


def _root() -> Path:
    fallback = _fallback_root()
    if fallback is not None:
        return fallback

    from wade.git import repo as git_repo
    from wade.git.repo import GitError

    try:
        return git_repo.get_repo_root(Path.cwd())
    except GitError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None


@session_app.command("describe")
def describe_session() -> None:
    """Describe the frozen workflow and skill bindings for this worktree."""

    from wade.services.session_composition_service import load_session_manifest

    root = _root()
    manifest = load_session_manifest(root)
    if manifest is None:
        typer.echo("error: no readable active session manifest", err=True)
        raise typer.Exit(1)
    typer.echo(f"session={manifest.session.value}")
    typer.echo(f"workflow_revision={manifest.workflow_revision}")
    typer.echo(f"task_id={manifest.task_id or ''}")
    typer.echo(f"ai_command={manifest.ai_command.value}")
    for slot, binding in manifest.bindings.items():
        typer.echo(f"slot={slot.value} digest={binding.digest}")
        for index, skill in enumerate(binding.skills):
            typer.echo(
                f"  {index}: {skill.canonical_ref} {skill.content_digest} {skill.materialized_path}"
            )


@session_app.command("refresh-skills")
def refresh_session_skills(
    skill: list[str] | None = typer.Option(None, "--skill"),  # noqa: B008
    review_skill: list[str] | None = typer.Option(None, "--review-skill"),  # noqa: B008
) -> None:
    """Explicitly rediscover and replace the active session's frozen snapshots."""

    from wade.config.loader import load_config
    from wade.git import repo as git_repo
    from wade.git.repo import GitError
    from wade.services.session_composition_service import (
        SessionCompositionError,
        compose_session,
        load_session_manifest,
    )
    from wade.skills.materializer import SkillMaterializationError
    from wade.skills.resolver import SkillResolutionError
    from wade.skills.validation import SkillValidationError

    root = _root()
    existing = load_session_manifest(root)
    if existing is None:
        typer.echo("error: no readable active session manifest", err=True)
        raise typer.Exit(1)
    fallback = _fallback_root()
    if fallback is None:
        config_root = root
        try:
            main_root = git_repo.main_checkout_root(root)
        except Exception:
            main_root = root
        display_root = ".wade/session"
    else:
        # The bundle is in a throwaway plan directory, but config and project
        # skills still belong to the checkout from which the parent launched
        # the agent. A caller outside Git deliberately uses its cwd as that
        # project root, matching initial fallback composition.
        config_root = Path.cwd()
        try:
            main_root = git_repo.get_repo_root(config_root)
        except GitError:
            main_root = config_root
        display_root = str(root / ".wade/session")
    config = load_config(config_root)
    try:
        result = compose_session(
            root,
            main_root,
            config,
            kind=existing.session,
            task_id=existing.task_id,
            work_skills=skill,
            review_skills=review_skill,
            refresh=True,
            display_root=display_root,
        )
    except (
        SessionCompositionError,
        SkillMaterializationError,
        SkillResolutionError,
        SkillValidationError,
    ) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"refreshed session={result.manifest.session.value}")
    for slot, binding in result.manifest.bindings.items():
        typer.echo(f"slot={slot.value} digest={binding.digest}")
