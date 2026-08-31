"""Review subcommands — plan review, implementation review, and PR comment review."""

from __future__ import annotations

import typer

from wade.cli.autocomplete import (
    complete_ai_tools,
    complete_delegation_modes,
    complete_effort_levels,
    complete_models,
    complete_permission_modes,
)
from wade.models.delegation import DelegationMode, DelegationResult

review_app = typer.Typer(
    help="AI-powered review commands.",
    invoke_without_command=True,
)


def _finalize_review_result(
    result: DelegationResult,
    complete_message: str,
    *,
    self_review_followup: str | None = None,
) -> None:
    """Print status message and exit with the appropriate code.

    Shared by plan, implementation, and batch review commands.
    """
    from wade.ui.console import console

    if result.success and not result.skipped:
        if result.mode == DelegationMode.PROMPT:
            console.info(
                "SELF-REVIEW — read the review prompt above, perform the review "
                "yourself, and address any issues before proceeding."
                + (f" {self_review_followup}" if self_review_followup else "")
            )
        else:
            console.info(complete_message)

    if not result.success:
        raise typer.Exit(1)
    if not result.skipped and result.mode == DelegationMode.PROMPT:
        raise typer.Exit(2)
    raise typer.Exit(0)


@review_app.callback()
def review_callback(ctx: typer.Context) -> None:
    """Show help when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


@review_app.command("plan")
def review_plan_cmd(
    plan_file: str = typer.Argument(..., help="Path to the plan file to review."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    mode: DelegationMode | None = typer.Option(  # noqa: B008
        None,
        "--mode",
        help="Delegation mode: prompt, interactive, headless.",
        autocompletion=complete_delegation_modes,
    ),
    effort: str | None = typer.Option(
        None, "--effort", help="Effort level for AI.", autocompletion=complete_effort_levels
    ),
    yolo: bool = typer.Option(False, "--yolo", help="Skip AI tool permission prompts."),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Autonomy tier: default, accept-edits, auto, or yolo.",
        autocompletion=complete_permission_modes,
    ),
    skill: list[str] | None = typer.Option(  # noqa: B008
        None, "--skill", help="Review methodology skill ref. Repeat for an ordered binding."
    ),
) -> None:
    """Review a plan file."""
    from wade.services.review_delegation_service import review_plan

    result = review_plan(
        plan_file,
        ai_tool=ai,
        model=model,
        mode=mode.value if mode else None,
        effort=effort,
        ai_explicit=ai is not None,
        model_explicit=model is not None,
        effort_explicit=effort is not None,
        yolo=yolo or None,
        permission_mode=permission_mode,
        permission_mode_explicit=permission_mode is not None,
        skills=skill,
    )
    _finalize_review_result(
        result,
        "REVIEW COMPLETE — address any actionable feedback above, "
        "then proceed to wade plan-session done.",
    )


@review_app.command("implementation")
def review_implementation_cmd(
    staged: bool = typer.Option(False, "--staged", help="Review only staged changes."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    mode: DelegationMode | None = typer.Option(  # noqa: B008
        None,
        "--mode",
        help="Delegation mode: prompt, interactive, headless.",
        autocompletion=complete_delegation_modes,
    ),
    effort: str | None = typer.Option(
        None, "--effort", help="Effort level for AI.", autocompletion=complete_effort_levels
    ),
    yolo: bool = typer.Option(False, "--yolo", help="Skip AI tool permission prompts."),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Autonomy tier: default, accept-edits, auto, or yolo.",
        autocompletion=complete_permission_modes,
    ),
    skill: list[str] | None = typer.Option(  # noqa: B008
        None, "--skill", help="Review methodology skill ref. Repeat for an ordered binding."
    ),
    ack_self_review: bool = typer.Option(
        False,
        "--ack-self-review",
        help="After prompt-mode self-review, write its binding-aware completion receipt.",
    ),
) -> None:
    """Review code changes."""
    from wade.services.review_delegation_service import review_implementation

    result = review_implementation(
        staged=staged,
        ai_tool=ai,
        model=model,
        mode=mode.value if mode else None,
        effort=effort,
        ai_explicit=ai is not None,
        model_explicit=model is not None,
        effort_explicit=effort is not None,
        yolo=yolo or None,
        permission_mode=permission_mode,
        permission_mode_explicit=permission_mode is not None,
        skills=skill,
        ack_self_review=ack_self_review,
    )
    if ack_self_review:
        if result.success and not result.skipped:
            from wade.ui.console import console

            console.info(result.feedback)
        raise typer.Exit(0 if result.success else 1)
    _finalize_review_result(
        result,
        "REVIEW COMPLETE — address any actionable feedback above, "
        "then proceed to wade implementation-session done.",
        self_review_followup=(
            "When the self-review is complete, run "
            "`wade review implementation --ack-self-review` to write the receipt."
        ),
    )


@review_app.command("pr-comments")
def review_pr_comments_cmd(
    target: str = typer.Argument(..., help="Issue number."),
    ai: list[str] | None = typer.Option(  # noqa: B008
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    detach: bool = typer.Option(False, "--detach", help="Launch AI in a new terminal."),
    yolo: bool = typer.Option(False, "--yolo", help="Skip AI tool permission prompts."),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Autonomy tier: default, accept-edits, auto, or yolo.",
        autocompletion=complete_permission_modes,
    ),
    network_access: bool | None = typer.Option(
        None,
        "--network/--no-network",
        help="Allow network access inside the Codex sandbox (default: off; "
        "required for git fetch/push under Codex). Overrides ai.network_access.",
    ),
    skill: list[str] | None = typer.Option(  # noqa: B008
        None, "--skill", help="WORK methodology skill ref. Repeat for an ordered binding."
    ),
    review_skill: list[str] | None = typer.Option(  # noqa: B008
        None, "--review-skill", help="Closing review skill ref. Repeat for an ordered binding."
    ),
    refresh_skills: bool = typer.Option(
        False, "--refresh-skills", help="Explicitly replace a resumed session's frozen skills."
    ),
) -> None:
    """Address PR review comments."""
    from wade.services.review_service import start as do_start
    from wade.ui import prompts

    selected_ai = prompts.resolve_ai_from_list(ai)

    success = do_start(
        target=target,
        ai_tool=selected_ai,
        model=model,
        detach=detach,
        ai_explicit=selected_ai is not None,
        model_explicit=model is not None,
        yolo=yolo or None,
        permission_mode=permission_mode,
        permission_mode_explicit=permission_mode is not None,
        network_access=network_access,
        work_skills=skill,
        review_skills=review_skill,
        refresh_skills=refresh_skills,
    )
    raise typer.Exit(0 if success else 1)


@review_app.command("trigger")
def review_trigger_cmd(
    target: str = typer.Argument(..., help="Issue number."),
    bot: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--bot",
        help="Trigger only the named bot(s). Repeatable. Overrides `enabled: false`.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what would be posted without posting."
    ),
) -> None:
    """Post configured bot-review trigger comments on the issue's PR."""
    from wade.services.review_service import trigger_bot_reviews

    report = trigger_bot_reviews(target, selected_bots=bot, dry_run=dry_run)
    raise typer.Exit(report.exit_code)


@review_app.command("batch")
def review_batch_cmd(
    tracking_issue: int = typer.Argument(..., help="Tracking issue number."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    mode: DelegationMode | None = typer.Option(  # noqa: B008
        None,
        "--mode",
        help="Delegation mode: prompt, interactive, headless.",
        autocompletion=complete_delegation_modes,
    ),
    effort: str | None = typer.Option(
        None, "--effort", help="Effort level for AI.", autocompletion=complete_effort_levels
    ),
    yolo: bool = typer.Option(False, "--yolo", help="Skip AI tool permission prompts."),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Autonomy tier: default, accept-edits, auto, or yolo.",
        autocompletion=complete_permission_modes,
    ),
    skill: list[str] | None = typer.Option(  # noqa: B008
        None, "--skill", help="Batch review methodology skill ref. Repeat for an ordered binding."
    ),
) -> None:
    """Run coherence review on a batch of parallel implementation branches."""
    from wade.services.batch_review_service import review_batch

    result = review_batch(
        str(tracking_issue),
        ai_tool=ai,
        model=model,
        mode=mode.value if mode else None,
        effort=effort,
        ai_explicit=ai is not None,
        model_explicit=model is not None,
        effort_explicit=effort is not None,
        yolo=yolo or None,
        permission_mode=permission_mode,
        permission_mode_explicit=permission_mode is not None,
        skills=skill,
    )
    _finalize_review_result(
        result,
        "BATCH REVIEW COMPLETE — check the draft PR for combined diff and findings.",
    )
