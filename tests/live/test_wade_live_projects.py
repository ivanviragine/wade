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
from wade.models.project import ProjectRequest
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
    required = {operation.field_id for operation in preview.operations}
    try:
        applied = execute(request.model_copy(update={"apply": True}), cwd=cwd)
        assert applied.success and all(
            operation.status in {"confirmed", "noop"} for operation in applied.operations
        )
        again = execute(request.model_copy(update={"apply": True}), cwd=cwd)
        assert again.success and all(operation.status == "noop" for operation in again.operations)
    finally:
        assert settings.github_projects is not None
        for operation in preview.operations:
            current = provider.item(preview.project.id, item_id, required)
            value = current.values.get(operation.field_id)
            if value is not None and value.value == operation.new and operation.old is not None:
                provider.update_number(
                    preview.project.id, item_id, operation.field_id, operation.old
                )
            elif value is None or value.value != operation.old:
                pytest.fail(
                    f"Concurrent change on {item_id}/{operation.field_id}; preserved it. "
                    "Inspect the disposable board manually."
                )
