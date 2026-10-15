"""Opt-in WADE Projects behavior on a disposable, explicitly authorized board.

Reads require RUN_LIVE_GH_TESTS=1 and WADE_LIVE_PROJECT_REPO with Projects config.
Writes additionally require WADE_LIVE_PROJECT_WRITES=1 and WADE_LIVE_PROJECT_ITEM.
No items, fields, issues or projects are created/deleted. Existing numeric output
values are restored only if they still equal this test's expected write.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wade.config.loader import load_config
from wade.models.project import ProjectRequest, ProjectResult
from wade.providers.github_projects import GitHubProjectsProvider
from wade.services.project_service import execute

pytestmark = [
    pytest.mark.live_gh,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_GH_TESTS") != "1" or not os.environ.get("WADE_LIVE_PROJECT_REPO"),
        reason="Explicit disposable Projects live configuration required.",
    ),
]


def test_live_wade_project_reads() -> None:
    cwd = Path(os.environ["WADE_LIVE_PROJECT_REPO"])
    listed = execute(ProjectRequest(operation="items"), cwd=cwd)
    ranked = execute(ProjectRequest(operation="rank"), cwd=cwd)
    preview = execute(ProjectRequest(operation="sync", dry_run=True), cwd=cwd)
    assert listed.project.id == ranked.project.id == preview.project.id
    assert not listed.applied and not ranked.applied and not preview.applied
    assert all(operation.status in {"planned", "noop"} for operation in preview.operations)


@pytest.mark.skipif(
    os.environ.get("WADE_LIVE_PROJECT_WRITES") != "1"
    or not os.environ.get("WADE_LIVE_PROJECT_ITEM"),
    reason="Additional explicit write opt-in and disposable item ID required.",
)
def test_live_wade_numeric_apply_and_idempotent_rerun() -> None:
    cwd = Path(os.environ["WADE_LIVE_PROJECT_REPO"])
    item_id = os.environ["WADE_LIVE_PROJECT_ITEM"]
    request = ProjectRequest(operation="sync", filters=[f"id={item_id}"])
    preview = execute(request, cwd=cwd)
    assert len(preview.rows) == 1 and preview.rows[0].item.id == item_id
    assert preview.operations and all(
        operation.old is not None for operation in preview.operations
    ), "Disposable fixture outputs must have existing numeric values for guarded cleanup."
    assert any(operation.status == "planned" for operation in preview.operations), (
        "Disposable fixture must require an actual numeric update."
    )
    settings = load_config(cwd)
    provider = GitHubProjectsProvider(cwd, preview.project.hostname)
    attempts: list[ProjectResult] = []
    apply_pending = False
    try:
        apply_pending = True
        applied = execute(request.model_copy(update={"apply": True}), cwd=cwd)
        attempts.append(applied)
        apply_pending = False
        assert applied.success and all(
            operation.status in {"confirmed", "noop"} for operation in applied.operations
        )
        apply_pending = True
        again = execute(request.model_copy(update={"apply": True}), cwd=cwd)
        attempts.append(again)
        apply_pending = False
        assert again.success and all(operation.status == "noop" for operation in again.operations)
    finally:
        assert settings.github_projects is not None
        if apply_pending:
            pytest.fail(
                "Apply outcome unavailable; preserved board state. "
                "Inspect the disposable board manually."
            )
        for attempt in reversed(attempts):
            required = {operation.field_id for operation in attempt.operations}
            for operation in reversed(attempt.operations):
                if operation.status not in {"confirmed", "unknown"}:
                    continue
                current = provider.item(attempt.project.id, item_id, required)
                value = current.values.get(operation.field_id)
                actual = value.value if value is not None else None
                if actual == operation.old:
                    continue
                if actual == operation.new and operation.old is not None:
                    provider.update_number(
                        attempt.project.id, item_id, operation.field_id, operation.old
                    )
                else:
                    pytest.fail(
                        "Concurrent change or unavailable baseline on "
                        f"{item_id}/{operation.field_id}; preserved it. "
                        "Inspect the disposable board manually."
                    )
