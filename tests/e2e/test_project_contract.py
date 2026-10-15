"""Real Projects CLI contracts with stateful gh JSON request and mutation audits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.e2e._support import MockGhCli, _git, _parse_json_output, _run

pytestmark = [pytest.mark.e2e_docker, pytest.mark.contract]


@pytest.fixture
def project_board(e2e_repo: Path, mock_gh_cli: MockGhCli, monkeypatch: pytest.MonkeyPatch):
    config = yaml.safe_load((e2e_repo / ".wade.yml").read_text())
    config["github_projects"] = {
        "target": {"owner": "test", "kind": "organization", "number": 1, "hostname": "github.com"},
        "fields": {
            "impact": {"id": "IMPACT", "type": "NUMBER"},
            "status": {"id": "STATUS", "type": "SINGLE_SELECT"},
            "computed_score": {"id": "SCORE", "type": "NUMBER"},
            "computed_rank": {"id": "RANK", "type": "NUMBER"},
        },
        "columns": ["repository", "number", "title", "impact"],
        "ranking": {
            "factors": [
                {"field": "impact", "minimum": 0, "maximum": 3, "missing": {"policy": "exclude"}}
            ]
        },
        "wip": {
            "status": "status",
            "ready": ["READY"],
            "in_progress": ["DOING"],
            "pool": "project",
            "limit": 1,
        },
        "outputs": {"score": "computed_score", "rank": "computed_rank"},
    }
    (e2e_repo / ".wade.yml").write_text(yaml.safe_dump(config))
    _git(["remote", "add", "origin", "https://github.com/test/e2e-project.git"], e2e_repo)
    state = json.loads(mock_gh_cli["state_file"].read_text())
    items, values = [], {}
    for number in (1, 2):
        items.append(
            {
                "id": f"I{number}",
                "project": {"id": "BOARD"},
                "isArchived": False,
                "content": {
                    "__typename": "Issue",
                    "id": f"C{number}",
                    "number": number,
                    "title": f"Task {number}",
                    "url": f"https://github.com/test/e2e-project/issues/{number}",
                    "state": "OPEN",
                    "repository": {"nameWithOwner": "test/e2e-project"},
                },
            }
        )
        values[f"I{number}"] = [
            {
                "__typename": "ProjectV2ItemFieldNumberValue",
                "field": {"id": "IMPACT"},
                "number": number,
            },
            {
                "__typename": "ProjectV2ItemFieldSingleSelectValue",
                "field": {"id": "STATUS"},
                "optionId": "READY",
                "name": "Ready",
            },
        ]
    state["projects"] = {
        "project": {
            "id": "BOARD",
            "title": "Test board",
            "url": "https://github.com/orgs/test/projects/1",
            "viewerCanUpdate": True,
        },
        "fields": [
            {"id": "IMPACT", "name": "Impact", "dataType": "NUMBER", "isIssueField": False},
            {
                "id": "STATUS",
                "name": "Status",
                "dataType": "SINGLE_SELECT",
                "isIssueField": False,
                "options": [{"id": "READY", "name": "Ready"}, {"id": "DOING", "name": "Doing"}],
            },
            {"id": "SCORE", "name": "Score", "dataType": "NUMBER", "isIssueField": False},
            {"id": "RANK", "name": "Rank", "dataType": "NUMBER", "isIssueField": False},
        ],
        "items": items,
        "values": values,
        "writes": [],
    }
    mock_gh_cli["state_file"].write_text(json.dumps(state))
    audit = e2e_repo / "projects-requests.jsonl"
    monkeypatch.setenv("WADE_MOCK_PROJECTS_LOG", str(audit))
    return audit


@pytest.mark.parametrize(
    "args",
    [
        ["items", "--sort", "impact:desc"],
        ["rank"],
        ["pick", "--count", "2"],
        ["sync"],
        ["sync", "--dry-run"],
    ],
)
def test_read_paths_single_json_document_and_zero_mutations(
    e2e_repo: Path, mock_gh_cli: MockGhCli, project_board: Path, args: list[str]
) -> None:
    result = _run(["project", *args, "--json"], e2e_repo)
    assert result.returncode == 0, result.stderr
    payload = _parse_json_output(result.stdout)
    assert payload["project"]["id"] == "BOARD"
    assert payload["rows"][0]["item"]["number"] == 2
    assert payload["rows"][0]["item"]["repository"] == "test/e2e-project"
    if args[0] == "pick":
        assert payload["current_wip"] == 0 and payload["available_slots"] == 1
        assert len(payload["rows"]) == 1
    requests = [json.loads(line) for line in project_board.read_text().splitlines()]
    assert requests and not any(request["query"].startswith("mutation") for request in requests)
    assert json.loads(mock_gh_cli["state_file"].read_text())["projects"]["writes"] == []


def test_apply_matches_preview_fractional_json_and_second_apply_noops(
    e2e_repo: Path, mock_gh_cli: MockGhCli, project_board: Path
) -> None:
    preview = _parse_json_output(_run(["project", "sync", "--json"], e2e_repo).stdout)
    first = _run(["project", "sync", "--apply", "--json"], e2e_repo)
    assert first.returncode == 0, first.stderr
    applied = _parse_json_output(first.stdout)
    assert [(o["item_id"], o["field_id"], o["new"]) for o in applied["operations"]] == [
        (o["item_id"], o["field_id"], o["new"]) for o in preview["operations"]
    ]
    assert all(o["status"] == "confirmed" for o in applied["operations"])
    writes = [
        json.loads(line)["variables"]
        for line in project_board.read_text().splitlines()
        if json.loads(line)["query"].startswith("mutation")
    ]
    assert len(writes) == 4 and any(
        w["number"] == 33.333333 and isinstance(w["number"], float) for w in writes
    )
    second = _run(["project", "sync", "--apply", "--json"], e2e_repo)
    assert second.returncode == 0 and all(
        o["status"] == "noop" for o in _parse_json_output(second.stdout)["operations"]
    )
    assert len(json.loads(mock_gh_cli["state_file"].read_text())["projects"]["writes"]) == 4


@pytest.mark.parametrize(
    "mode,statuses,writes",
    [
        ("drift_before_apply", ["not_attempted"] * 4, 0),
        ("fail_write_at", ["confirmed", "unknown", "not_attempted", "not_attempted"], 1),
    ],
)
def test_apply_drift_or_partial_failure_truthful_nonzero(
    e2e_repo: Path,
    mock_gh_cli: MockGhCli,
    project_board: Path,
    mode: str,
    statuses: list[str],
    writes: int,
) -> None:
    state = json.loads(mock_gh_cli["state_file"].read_text())
    state["projects"][mode] = True if mode == "drift_before_apply" else 1
    mock_gh_cli["state_file"].write_text(json.dumps(state))
    result = _run(["project", "sync", "--apply", "--json"], e2e_repo)
    assert result.returncode == 1, result.stderr
    payload = _parse_json_output(result.stdout)
    assert not payload["success"] and [o["status"] for o in payload["operations"]] == statuses
    assert len(json.loads(mock_gh_cli["state_file"].read_text())["projects"]["writes"]) == writes


def test_valid_empty_board_and_invalid_paths_never_write(
    e2e_repo: Path, mock_gh_cli: MockGhCli, project_board: Path
) -> None:
    state = json.loads(mock_gh_cli["state_file"].read_text())
    state["projects"]["items"] = []
    mock_gh_cli["state_file"].write_text(json.dumps(state))
    result = _run(["project", "rank", "--json"], e2e_repo)
    assert result.returncode == 0 and _parse_json_output(result.stdout)["rows"] == []
    for args in (
        ["sync", "--apply", "--dry-run"],
        ["items", "--columns", "missing"],
        ["items", "--owner", "partial"],
    ):
        result = _run(["project", *args, "--json"], e2e_repo)
        assert result.returncode != 0 and result.stdout == ""
    config = yaml.safe_load((e2e_repo / ".wade.yml").read_text())
    config["provider"] = {"name": "markdown"}
    (e2e_repo / ".wade.yml").write_text(yaml.safe_dump(config))
    result = _run(["project", "items", "--json"], e2e_repo)
    assert result.returncode == 1 and "provider.name: github" in result.stderr
    assert not any(
        json.loads(line)["query"].startswith("mutation")
        for line in project_board.read_text().splitlines()
    )


def test_issue_backed_numeric_output_refuses_before_any_mutation(
    e2e_repo: Path, mock_gh_cli: MockGhCli, project_board: Path
) -> None:
    state = json.loads(mock_gh_cli["state_file"].read_text())
    state["projects"]["fields"][-1]["isIssueField"] = True
    mock_gh_cli["state_file"].write_text(json.dumps(state))
    result = _run(["project", "sync", "--apply", "--json"], e2e_repo)
    assert result.returncode == 1 and result.stdout == ""
    assert "project-owned" in result.stderr
    requests = [json.loads(line) for line in project_board.read_text().splitlines()]
    assert any("isIssueField" in request["query"] for request in requests)
    assert not any(request["query"].startswith("mutation") for request in requests)
    assert json.loads(mock_gh_cli["state_file"].read_text())["projects"]["writes"] == []


@pytest.mark.parametrize("partial_failure", [False, True])
def test_human_apply_preserves_external_title_and_write_outcomes(
    e2e_repo: Path, mock_gh_cli: MockGhCli, project_board: Path, partial_failure: bool
) -> None:
    state = json.loads(mock_gh_cli["state_file"].read_text())
    state["projects"]["items"][0]["content"]["title"] = "Handle [/bold]"
    if partial_failure:
        state["projects"]["fail_write_at"] = 1
    mock_gh_cli["state_file"].write_text(json.dumps(state))
    result = _run(["project", "sync", "--apply"], e2e_repo)
    assert result.returncode == (1 if partial_failure else 0)
    assert "Handle [/bold]" in result.stdout and "confirmed" in result.stdout
    if partial_failure:
        assert "unknown" in result.stdout and "not_attempted" in result.stdout
        assert "GitHub Projects request failed" in result.stderr
    assert len(json.loads(mock_gh_cli["state_file"].read_text())["projects"]["writes"]) == (
        1 if partial_failure else 4
    )


def test_default_pick_preserves_canonical_repository_case_without_mutations(
    e2e_repo: Path, mock_gh_cli: MockGhCli, project_board: Path
) -> None:
    state = json.loads(mock_gh_cli["state_file"].read_text())
    for item in state["projects"]["items"]:
        item["content"]["repository"]["nameWithOwner"] = "Test/E2E-Project"
    mock_gh_cli["state_file"].write_text(json.dumps(state))
    result = _run(["project", "pick", "--json"], e2e_repo)
    assert result.returncode == 0, result.stderr
    assert [row["item"]["repository"] for row in _parse_json_output(result.stdout)["rows"]] == [
        "Test/E2E-Project"
    ]
    assert json.loads(mock_gh_cli["state_file"].read_text())["projects"]["writes"] == []
    assert not any(
        json.loads(line)["query"].startswith("mutation")
        for line in project_board.read_text().splitlines()
    )
