"""Mock-backed live cleanup races never require provider credentials or writes."""

from unittest.mock import patch

import pytest

from tests.live import test_wade_live_projects as live
from wade.models.project import ProjectItem, ProjectResult, ProjectValue


def result(old, *, status="planned", success=True):
    return ProjectResult.model_validate(
        {
            "operation": "sync",
            "project": {
                "id": "BOARD",
                "owner": "org",
                "kind": "organization",
                "number": 1,
                "hostname": "github.com",
                "title": "Board",
                "url": "https://github.com/orgs/org/projects/1",
                "viewer_can_update": True,
            },
            "columns": ["id"],
            "rows": [
                {
                    "item": {"id": "I1", "project_id": "BOARD", "kind": "Issue"},
                    "values": {"id": "I1"},
                }
            ],
            "operations": [
                {
                    "item_id": "I1",
                    "identity": "org/repo:Issue:1",
                    "field_id": "SCORE",
                    "field": "score",
                    "old": old,
                    "new": 100,
                    "status": status,
                }
            ],
            "visibility": "fixture",
            "success": success,
        }
    )


class Provider:
    def __init__(self):
        self.value = 100
        self.writes = []

    def item(self, project_id, item_id, required):
        return ProjectItem(
            id=item_id,
            project_id=project_id,
            kind="Issue",
            values={
                "SCORE": ProjectValue(
                    field_id="SCORE", type="ProjectV2ItemFieldNumberValue", value=self.value
                )
            },
        )

    def update_number(self, project_id, item_id, field_id, number):
        self.writes.append(number)
        self.value = number


def invoke(provider, results, monkeypatch):
    monkeypatch.setenv("WADE_LIVE_PROJECT_REPO", "/tmp/mock-board")
    monkeypatch.setenv("WADE_LIVE_PROJECT_ITEM", "I1")
    with (
        patch.object(live, "execute", side_effect=results),
        patch.object(live, "GitHubProjectsProvider", return_value=provider),
        patch.object(live, "load_config"),
    ):
        live.test_live_wade_numeric_apply_and_idempotent_rerun()


def test_cleanup_uses_apply_baseline_after_preview_drift(monkeypatch):
    provider = Provider()
    invoke(
        provider,
        [result(10), result(20, status="confirmed"), result(100, status="noop")],
        monkeypatch,
    )
    assert provider.writes == [20]


def test_cleanup_does_not_restore_noop_values(monkeypatch):
    provider = Provider()
    invoke(
        provider, [result(10), result(100, status="noop"), result(100, status="noop")], monkeypatch
    )
    assert provider.writes == []


def test_cleanup_preserves_post_apply_concurrent_change(monkeypatch):
    provider = Provider()
    provider.value = 55
    with pytest.raises(pytest.fail.Exception, match="Concurrent change"):
        invoke(
            provider,
            [result(10), result(20, status="confirmed"), result(100, status="noop")],
            monkeypatch,
        )
    assert provider.writes == [] and provider.value == 55


def test_cleanup_unwinds_each_actual_apply_baseline(monkeypatch):
    provider = Provider()
    with pytest.raises(pytest.fail.Exception, match="Concurrent change"):
        invoke(
            provider,
            [result(10), result(20, status="confirmed"), result(30, status="confirmed")],
            monkeypatch,
        )
    assert provider.writes == [30] and provider.value == 30


@pytest.mark.parametrize("status", ["failed", "not_attempted"])
def test_cleanup_leaves_unattempted_or_rejected_values_alone(monkeypatch, status):
    provider = Provider()
    with pytest.raises(AssertionError):
        invoke(provider, [result(10), result(20, status=status, success=False)], monkeypatch)
    assert provider.writes == [] and provider.value == 100


def test_cleanup_handles_unknown_attempt_using_its_actual_baseline(monkeypatch):
    provider = Provider()
    with pytest.raises(AssertionError):
        invoke(provider, [result(10), result(20, status="unknown", success=False)], monkeypatch)
    assert provider.writes == [20]


@pytest.mark.parametrize("second_apply", [False, True])
def test_cleanup_refuses_guessing_when_apply_returns_no_outcome(monkeypatch, second_apply):
    provider = Provider()
    results = [result(10)]
    if second_apply:
        results.append(result(20, status="confirmed"))
    results.append(RuntimeError("unexpected after possible mutation"))
    with pytest.raises(pytest.fail.Exception, match="outcome unavailable"):
        invoke(provider, results, monkeypatch)
    assert provider.writes == [] and provider.value == 100


def test_cleanup_preserves_write_when_actual_prior_value_is_missing(monkeypatch):
    provider = Provider()
    with pytest.raises(pytest.fail.Exception, match="unavailable baseline"):
        invoke(
            provider,
            [result(10), result(None, status="confirmed"), result(100, status="noop")],
            monkeypatch,
        )
    assert provider.writes == [] and provider.value == 100
