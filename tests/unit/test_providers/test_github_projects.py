"""Projects connection completeness, typed gh transport and strict error contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from wade.models.project import ProjectError, ProjectTarget
from wade.providers.github_projects import GitHubProjectsProvider


def connection(nodes, after=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": after is not None, "endCursor": after}}


def raw_item(number, *, content=True):
    return {
        "id": f"I{number}",
        "project": {"id": "BOARD"},
        "isArchived": False,
        "content": {
            "__typename": "Issue",
            "id": f"C{number}",
            "number": number,
            "title": f"Task {number}",
            "url": f"https://github.com/org/repo/issues/{number}",
            "state": "OPEN",
            "repository": {"nameWithOwner": "org/repo"},
            "labels": connection([{"id": f"L{number}", "name": "bug"}]),
            "assignees": connection([{"id": f"U{number}", "login": "dev"}]),
        }
        if content
        else None,
    }


def field_value(field_id, number=1):
    return {
        "__typename": "ProjectV2ItemFieldNumberValue",
        "field": {"id": field_id},
        "number": number,
    }


class API:
    def __init__(self):
        self.calls = []
        self.project = {
            "id": "BOARD",
            "title": "Board",
            "url": "https://github.com/orgs/org/projects/1",
            "viewerCanUpdate": True,
        }
        self.fields = [
            {"id": f"F{n}", "name": f"Field {n}", "dataType": "NUMBER", "isIssueField": False}
            for n in range(101)
        ]
        self.items = [raw_item(n) for n in range(1, 102)]
        self.values = {"I1": [field_value(f"F{n}", n) for n in range(101)]}
        self.override = None
        self.labels = [{"id": f"L{n}", "name": f"label-{n}"} for n in range(101)]
        self.users = [{"id": f"U{n}", "login": f"user-{n}"} for n in range(101)]

    def page(self, nodes, cursor):
        start = int(cursor) if cursor is not None else 0
        end = start + 100
        return connection(nodes[start:end], str(end) if end < len(nodes) else None)

    def __call__(self, args, **kwargs):
        assert args == ["gh", "api", "graphql", "--hostname", "ghe.example.com", "--input", "-"]
        assert kwargs["cwd"] == Path("/tmp/projects")
        assert kwargs["retries"] == 0 and not kwargs["check"]
        payload = json.loads(kwargs["input_text"])
        query, variables = payload["query"], payload["variables"]
        self.calls.append(payload)
        if self.override:
            result = self.override(query, variables)
            if result is not None:
                return result
        cursor = variables.get("after")
        node_id = variables.get("id")
        if query.startswith("mutation"):
            assert type(variables["number"]) in {int, float}
            data = {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["item"]}}}
        elif "projectV2(number:" in query:
            data = {"organization": {"projectV2": self.project}}
        elif "fields(first:" in query:
            data = {"node": {"id": "BOARD", "fields": self.page(self.fields, cursor)}}
        elif "items(first:" in query:
            assert "archivedStates:[ARCHIVED,NOT_ARCHIVED]" in query
            items = json.loads(json.dumps(self.items))
            items[0]["content"]["labels"] = self.page(self.labels, None)
            items[0]["content"]["assignees"] = self.page(self.users, None)
            data = {"node": {"id": "BOARD", "items": self.page(items, cursor)}}
        elif "fieldValues(first:" in query:
            data = {
                "node": {
                    "id": node_id,
                    "fieldValues": self.page(self.values.get(node_id, [field_value("F0")]), cursor),
                }
            }
        elif "labels(first:" in query:
            data = {"node": {"id": node_id, "labels": self.page(self.labels, cursor)}}
        elif "assignees(first:" in query:
            data = {"node": {"id": node_id, "assignees": self.page(self.users, cursor)}}
        else:
            data = {"node": self.items[int(node_id[1:]) - 1]}
        return subprocess.CompletedProcess(args, 0, json.dumps({"data": data}), "")


def provider():
    return GitHubProjectsProvider(Path("/tmp/projects"), "ghe.example.com", pause=lambda _: None)


def target():
    return ProjectTarget(owner="org", kind="organization", number=1, hostname="ghe.example.com")


def test_all_connections_exhausted_independently_beyond_100() -> None:
    api, client = API(), provider()
    with patch("wade.providers.github_projects.run", side_effect=api):
        board = client.schema(target())
        result = client.snapshot(board, {"F0", "F100"}, labels=True, assignees=True)
    assert len(board.fields) == 101 and len(result.items) == 101
    assert result.items[0].values["F100"].value == 100
    assert len(result.items[0].labels) == len(result.items[0].assignees) == 101
    second_pages = [
        (call["variables"].get("id"), call["variables"].get("after"), call["query"])
        for call in api.calls
        if call["variables"].get("after")
    ]
    assert any(i == "BOARD" and "fields(" in q for i, _, q in second_pages)
    assert any(i == "BOARD" and "items(" in q for i, _, q in second_pages)
    assert any(i == "I1" and "fieldValues(" in q for i, _, q in second_pages)
    assert any(i == "C1" and "labels(" in q for i, _, q in second_pages)
    assert any(i == "C1" and "assignees(" in q for i, _, q in second_pages)
    assert all(cursor == "100" for _, cursor, _ in second_pages)
    assert not any(call["query"].startswith("mutation") for call in api.calls)


def test_fractional_mutation_variable_is_json_number_not_string() -> None:
    api = API()
    with patch("wade.providers.github_projects.run", side_effect=api):
        provider().update_number("BOARD", "I1", "F0", 33.333333)
    assert api.calls[0]["variables"]["number"] == 33.333333
    assert isinstance(api.calls[0]["variables"]["number"], float)


@pytest.mark.parametrize(
    "bad",
    [
        {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "again"}},
        {"nodes": [{"id": "same"}, {"id": "same"}], "pageInfo": {"hasNextPage": False}},
        {"nodes": [{"id": "one"}], "pageInfo": {"hasNextPage": "false"}},
        {"nodes": [{"id": "one"}], "pageInfo": {"hasNextPage": True, "endCursor": None}},
        {"nodes": [None], "pageInfo": {"hasNextPage": False}},
    ],
)
def test_malformed_connections_never_return_partial_data(bad) -> None:
    with pytest.raises(ProjectError):
        provider()._connection(lambda _: bad)


def test_nonadvancing_cursor_and_resource_budgets() -> None:
    count = 0

    def fetch(_):
        nonlocal count
        count += 1
        return connection([{"id": str(count)}], "same")

    with pytest.raises(ProjectError, match="did not advance"):
        provider()._connection(fetch)
    for kwargs in ({"max_pages": 1}, {"max_nodes": 0}):
        client = GitHubProjectsProvider(Path("."), "github.com", **kwargs)
        with pytest.raises(ProjectError, match="budget"):
            client._connection(lambda _: connection([{"id": "one"}], "next"))
    client = GitHubProjectsProvider(Path("."), "github.com", max_requests=0)
    with pytest.raises(ProjectError, match="budget"):
        client.schema(target())


@pytest.mark.parametrize(
    "stdout,stderr,returncode,message",
    [
        ('{"data":{"organization":{}},"errors":[{"message":"partial"}]}', "", 0, "partial"),
        ('{"data":null}', "", 0, "Incomplete"),
        ('{"data":{"organization":{"projectV2":null}}}', "", 0, "project"),
        ("not json", "", 0, "malformed"),
        ("", "HTTP 403 insufficient scope", 1, "denied"),
        ("", "secondary rate limit", 1, "rate limit"),
        ('{"errors":[{"type":"FORBIDDEN"}],"data":{}}', "", 0, "denied"),
    ],
)
def test_process_graphql_auth_rate_and_missing_target_fail(
    stdout, stderr, returncode, message
) -> None:
    with (
        patch(
            "wade.providers.github_projects.run",
            return_value=subprocess.CompletedProcess([], returncode, stdout, stderr),
        ),
        pytest.raises(ProjectError, match=message),
    ):
        provider().schema(target())


def test_bounded_read_retry_but_never_mutation_retry() -> None:
    transient = subprocess.CompletedProcess([], 1, "", "HTTP 503")
    good = subprocess.CompletedProcess([], 0, '{"data":{"ok":true}}', "")
    with patch("wade.providers.github_projects.run", side_effect=[transient, good]) as run:
        assert provider()._graphql("query{}", {}) == {"ok": True}
        assert run.call_count == 2
    with patch(
        "wade.providers.github_projects.run", side_effect=subprocess.TimeoutExpired("gh", 30)
    ) as run:
        with pytest.raises(ProjectError) as error:
            provider().update_number("BOARD", "I1", "F0", 1)
        assert error.value.unknown_outcome and run.call_count == 1
    with patch(
        "wade.providers.github_projects.run", side_effect=subprocess.TimeoutExpired("gh", 30)
    ) as run:
        with pytest.raises(ProjectError):
            provider().schema(target())
        assert run.call_count == 2


def test_mutation_unexpected_identity_and_partial_errors_unknown() -> None:
    for payload in (
        {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "OTHER"}}}},
        {"data": {}, "errors": [{"message": "unknown"}]},
    ):
        with patch(
            "wade.providers.github_projects.run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
        ):
            with pytest.raises(ProjectError) as error:
                provider().update_number("BOARD", "I1", "F0", 1)
            assert error.value.unknown_outcome


def test_unavailable_content_explicit_and_unknown_union_fails() -> None:
    client = provider()
    with patch.object(client, "_values", return_value={}):
        unavailable = client._parse_item(raw_item(1, content=False), "BOARD", set(), False, False)
        assert unavailable.kind == "Unavailable" and not unavailable.available
        raw = raw_item(1)
        raw["content"]["__typename"] = "FutureContent"
        with pytest.raises(ProjectError, match="Unsupported"):
            client._parse_item(raw, "BOARD", set(), False, False)


@pytest.mark.parametrize(
    "raw", [field_value("F0", True), field_value("F0", float("nan")), {"__typename": "NewValue"}]
)
def test_invalid_numeric_or_unbound_union_cannot_be_missing_value(raw) -> None:
    api, client = API(), provider()
    api.values = {"I1": [raw]}
    with patch("wade.providers.github_projects.run", side_effect=api), pytest.raises(ProjectError):
        client._values("I1", {"F0"})


def test_two_item_field_connections_each_start_at_their_own_first_page() -> None:
    api, client = API(), provider()
    api.items = api.items[:2]
    api.values["I2"] = [field_value(f"F{n}", 1000 + n) for n in range(101)]
    with patch("wade.providers.github_projects.run", side_effect=api):
        board = client.schema(target())
        result = client.snapshot(board, {"F100"})
    assert [item.values["F100"].value for item in result.items] == [100, 1100]
    cursors = [
        (call["variables"]["id"], call["variables"]["after"])
        for call in api.calls
        if "fieldValues(first:" in call["query"]
    ]
    assert cursors == [("I1", None), ("I1", "100"), ("I2", None), ("I2", "100")]


@pytest.mark.parametrize(
    "typename,payload,expected,ids",
    [
        ("DateValue", {"date": "2026-10-14"}, "2026-10-14", []),
        ("TextValue", {"text": "Exact text"}, "Exact text", []),
        ("SingleSelectValue", {"optionId": "O", "name": "Option"}, "Option", ["O"]),
        (
            "MultiSelectValue",
            {"options": [{"id": "B", "name": "Beta"}, {"id": "A", "name": "Alpha"}]},
            ["Alpha", "Beta"],
            ["A", "B"],
        ),
        ("IterationValue", {"iterationId": "IT", "title": "Sprint"}, "Sprint", []),
        ("MilestoneValue", {"milestone": {"title": "Milestone"}}, "Milestone", []),
        ("RepositoryValue", {"repository": {"nameWithOwner": "org/repo"}}, "org/repo", []),
    ],
)
def test_supported_typed_field_values(typename, payload, expected, ids) -> None:
    api, client = API(), provider()
    client._multi_select = True
    api.values = {
        "I1": [{"__typename": f"ProjectV2ItemField{typename}", "field": {"id": "F0"}, **payload}]
    }
    with patch("wade.providers.github_projects.run", side_effect=api):
        value = client._values("I1", {"F0"})["F0"]
    assert value.value == expected and value.option_ids == ids and value.available


def test_unknown_bound_union_preserved_unavailable_and_invalid_date_rejected() -> None:
    api, client = API(), provider()
    api.values = {"I1": [{"__typename": "ProjectV2ItemIssueFieldValue", "field": {"id": "F0"}}]}
    with patch("wade.providers.github_projects.run", side_effect=api):
        assert not client._values("I1", {"F0"})["F0"].available
    api.values = {
        "I1": [
            {"__typename": "ProjectV2ItemFieldDateValue", "field": {"id": "F0"}, "date": "2026-1-1"}
        ]
    }
    with (
        patch("wade.providers.github_projects.run", side_effect=api),
        pytest.raises(ProjectError, match="ISO date"),
    ):
        client._values("I1", {"F0"})


def test_unmapped_multiselect_identity_does_not_require_new_union_payload() -> None:
    def api(args, **kwargs):
        query = json.loads(kwargs["input_text"])["query"]
        unselected = {"__typename": "ProjectV2ItemFieldMultiSelectValue"}
        # Model the actual GraphQL projection, not an overgenerous fake payload.
        if "... on ProjectV2ItemFieldValueCommon" in query:
            unselected["field"] = {"id": "UNSELECTED"}
        data = {"node": {"id": "I1", "fieldValues": connection([field_value("F0"), unselected])}}
        return subprocess.CompletedProcess(args, 0, json.dumps({"data": data}), "")

    client = provider()
    with patch("wade.providers.github_projects.run", side_effect=api):
        values = client._values("I1", {"F0"})
    assert values["F0"].value == 1 and set(values) == {"F0"}
    assert not client._multi_select


def test_schema_retains_issue_field_ownership() -> None:
    api = API()
    api.fields[0]["isIssueField"] = True
    with patch("wade.providers.github_projects.run", side_effect=api):
        provider = GitHubProjectsProvider(Path("/tmp/projects"), "ghe.example.com")
        board = provider.schema(ProjectTarget(owner="org", kind="organization", number=1))
    assert getattr(board.fields[0], "is_issue_field", None) is True
    assert "isIssueField" in next(
        call["query"] for call in api.calls if "fields(first:" in call["query"]
    )


@pytest.mark.parametrize("ownership", [None, "false", 0])
def test_schema_refuses_missing_or_malformed_ownership(ownership) -> None:
    api = API()
    if ownership is None:
        api.fields[0].pop("isIssueField")
    else:
        api.fields[0]["isIssueField"] = ownership
    with patch("wade.providers.github_projects.run", side_effect=api):
        provider = GitHubProjectsProvider(Path("/tmp/projects"), "ghe.example.com")
        with pytest.raises(ProjectError, match="ownership metadata"):
            provider.schema(ProjectTarget(owner="org", kind="organization", number=1))
    assert not any(call["query"].startswith("mutation") for call in api.calls)
