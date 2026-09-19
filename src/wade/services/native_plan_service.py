"""Consumer boundary for Crossby's collected planning API; no native adapters."""

from __future__ import annotations

import json
import re
from pathlib import Path

from crossby.ai_tools import AbstractAITool, terminal_interaction_handler
from crossby.models.ai import (
    PlanArtifactSource,
    PlanCommandPolicy,
    PlanInteraction,
    PlanInteractionOutcome,
    PlanInteractionResponse,
    PlanRequestBehavior,
    PlanSessionRequest,
    PlanSessionResult,
)
from pydantic import ValidationError

from wade.models.config import with_wade_base_pattern
from wade.models.plan_bundle import BUNDLE_MARKER, PlanBundle, PlanMember
from wade.models.task import PlanFile
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.safe_state import (
    StateFileAccessError,
    StateFileUnsafeError,
    exclusive_write_state_file,
    list_state_files,
    read_state_file_strict,
)

MAX_ARTIFACT_BYTES = 4_000_000


def failure_message(exc: BaseException) -> str:
    """Never echo Pydantic's input payload, which can contain plans or secrets."""
    from crossby.ai_tools.plan_mode import safe_error_excerpt

    if isinstance(exc, ValidationError):
        return str(exc.errors(include_input=False, include_context=False)[0]["msg"])
    return safe_error_excerpt(str(exc)) or type(exc).__name__


def prepare_request(
    tool: str,
    request: PlanSessionRequest,
    *,
    allowed_commands: list[str],
    confinement_required: bool = False,
    network_restriction_required: bool = False,
) -> PlanSessionRequest:
    """Preserve caller policy; capability validation and versions remain Crossby's."""
    adapter = AbstractAITool.get(tool)
    capability = adapter.capabilities().plan_mode
    if confinement_required and capability.sandbox_behavior is not PlanRequestBehavior.PRESERVED:
        raise ValueError(f"{tool} cannot guarantee explicit sandbox confinement for collection")
    if network_restriction_required and (
        not adapter.capabilities().supports_network_access or not request.sandbox
    ):
        raise ValueError(
            "The published collector cannot guarantee --no-network-access with this "
            "execution profile. Select a supported sandboxed collector."
        )
    if request.model and not adapter.is_model_compatible(request.model):
        raise ValueError(f"Selected model is not compatible with {tool}; select a supported model")
    return request.model_copy(
        update={
            "command_policy": PlanCommandPolicy(
                allowed_commands=tuple(with_wade_base_pattern(allowed_commands))
            ),
            "plan_output_dir": (
                request.working_dir / ".wade/plans/.native"
                if capability.artifact_source is PlanArtifactSource.REQUESTED_PATH
                else None
            ),
        }
    )


def interact(interaction: PlanInteraction) -> PlanInteractionResponse:
    """Present native IDs/constraints; Crossby validates and transports every response."""
    if not prompts.is_tty():
        return PlanInteractionResponse(outcome=PlanInteractionOutcome.SKIPPED)
    try:
        return terminal_interaction_handler(interaction)
    except EOFError:
        return PlanInteractionResponse(outcome=PlanInteractionOutcome.SKIPPED)
    except KeyboardInterrupt:
        return PlanInteractionResponse(outcome=PlanInteractionOutcome.CANCELLED)


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field in plan bundle")
        result[key] = value
    return result


def parse_artifact(markdown: str) -> PlanBundle:
    """Accept one Markdown plan or one explicit envelope, never infer task boundaries."""
    if not markdown.strip() or len(markdown.encode("utf-8")) > 2_000_000:
        raise ValueError("native plan is blank or exceeds the 2 MB import limit")
    if not markdown.strip().startswith("<!-- wade:plan-bundle:"):
        return PlanBundle(plans=(PlanMember(filename="PLAN.md", markdown=markdown),))
    match = re.fullmatch(
        re.escape(BUNDLE_MARKER) + r"\s*```json\s*\n(.*)\n```\s*", markdown.strip(), re.DOTALL
    )
    if match is None:
        raise ValueError("malformed or unsupported WADE plan bundle envelope")
    return PlanBundle.model_validate(json.loads(match[1], object_pairs_hook=_unique_keys))


def save_artifact(root: Path, result: PlanSessionResult) -> None:
    """Retain the exact public result, including Markdown and source provenance."""
    if not exclusive_write_state_file(
        root, ("plans",), "native-session.json", result.model_dump_json(indent=2)
    ):
        raise ValueError(
            "Cannot safely save native provenance; existing artifacts were not replaced"
        )


def load_artifact(root: Path) -> tuple[PlanSessionResult, PlanBundle]:
    """Load one retained collector result and verify its imported plan members."""
    try:
        raw = read_state_file_strict(
            root, ("plans",), "native-session.json", max_bytes=MAX_ARTIFACT_BYTES
        )
    except StateFileAccessError:
        raise
    except StateFileUnsafeError as exc:
        raise ValueError("Native planning handoff is absent or unsafe") from exc
    result = PlanSessionResult.model_validate_json(raw)
    bundle = parse_artifact(result.plan)
    validate_imported_set(root, bundle)
    return result, bundle


def materialize(root: Path, bundle: PlanBundle) -> None:
    """Import only named members, once; native source paths are never enumerated."""
    existing = list_state_files(root, ("plans",))
    if existing is None or any(
        name.startswith("PLAN") and name.endswith(".md") for name in existing
    ):
        raise ValueError("Plan import conflicts with an existing artifact; nothing was overwritten")
    for member in bundle.plans:
        if not exclusive_write_state_file(root, ("plans",), member.filename, member.markdown):
            raise ValueError(
                "Cannot safely materialize plan files; recover the retained native result"
            )


def validate_imported_set(root: Path, bundle: PlanBundle) -> None:
    """Review cannot add unrelated task files or silently delete bundle members."""
    files = list_state_files(root, ("plans",))
    if files is None or {
        name for name in files if name.startswith("PLAN") and name.endswith(".md")
    } != {member.filename for member in bundle.plans}:
        raise ValueError("Imported plan members changed; recover the retained native artifact")


def validate_selection(bundle: PlanBundle, plans: list[PlanFile]) -> set[str]:
    names = {plan.path.name for plan in plans}
    if any(
        not set(member.depends_on) <= names for member in bundle.plans if member.filename in names
    ):
        raise ValueError("The valid subset depends on an invalid plan; repair the retained bundle")
    return names


def review_materialized_plans(paths: list[Path], project_root: Path, *, yolo: bool) -> bool:
    """Run the fixed mapped review from the parent, then obtain the required decision."""
    from wade.models.delegation import DelegationMode
    from wade.services.review_delegation_service import review_plan

    for path in paths:
        result = review_plan(str(path), project_root=project_root)
        if not result.success:
            return False
        if result.skipped:
            continue
        if result.mode is DelegationMode.PROMPT:
            if not prompts.is_tty() or not prompts.confirm(
                "Have you performed the displayed self-review and addressed its findings?",
                default=False,
            ):
                console.error(
                    "Review is incomplete. Use a supported review mode or review retained plans."
                )
                return False
        elif (
            prompts.is_tty()
            and not yolo
            and not prompts.confirm(
                "Have review findings been addressed, and are these plans ready to create tasks?",
                default=False,
            )
        ):
            return False
    return True
