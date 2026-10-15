"""Strict, bounded Projects v2 GraphQL transport through gh-managed credentials."""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from wade.models.project import (
    ProjectDescriptor,
    ProjectError,
    ProjectField,
    ProjectItem,
    ProjectOption,
    ProjectSchema,
    ProjectSnapshot,
    ProjectTarget,
    ProjectValue,
)
from wade.utils.process import CommandError, run

_PAGE = "pageInfo { hasNextPage endCursor }"
_FIELDS = (
    """nodes {
  ... on ProjectV2FieldCommon { id name dataType isIssueField }
  ... on ProjectV2SingleSelectField { options { id name } }
} """
    + _PAGE
)
_VALUES = (
    """nodes { __typename
 ... on ProjectV2ItemFieldValueCommon { field { ... on ProjectV2FieldCommon { id } } }
 ... on ProjectV2ItemFieldDateValue { field { ... on ProjectV2FieldCommon { id } } date }
 ... on ProjectV2ItemFieldNumberValue { field { ... on ProjectV2FieldCommon { id } } number }
 ... on ProjectV2ItemFieldTextValue { field { ... on ProjectV2FieldCommon { id } } text }
 ... on ProjectV2ItemFieldSingleSelectValue { field { ... on ProjectV2FieldCommon { id } } optionId
 name }
 ... on ProjectV2ItemFieldIterationValue { field { ... on ProjectV2FieldCommon { id } } iterationId
 title }
 ... on ProjectV2ItemFieldLabelValue { field { ... on ProjectV2FieldCommon { id } } }
 ... on ProjectV2ItemFieldUserValue { field { ... on ProjectV2FieldCommon { id } } }
 ... on ProjectV2ItemFieldRepositoryValue { field { ... on ProjectV2FieldCommon { id } }
 repository {
 nameWithOwner } }
 ... on ProjectV2ItemFieldMilestoneValue { field { ... on ProjectV2FieldCommon { id } } milestone {
 title } }
 ... on ProjectV2ItemFieldPullRequestValue { field { ... on ProjectV2FieldCommon { id } } }
 ... on ProjectV2ItemIssueFieldValue { field { ... on ProjectV2FieldCommon { id } } }
 ... on ProjectV2ItemFieldReviewerValue { field { ... on ProjectV2FieldCommon { id } } }
} """
    + _PAGE
)


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectError(f"Incomplete Projects response: missing {context}.")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProjectError(f"Incomplete Projects response: invalid {context}.")
    return value


class GitHubProjectsProvider:
    """Read complete visible snapshots and update one numeric field at a time."""

    def __init__(
        self,
        cwd: Path,
        hostname: str,
        *,
        pause: Callable[[float], None] = time.sleep,
        max_pages: int = 100,
        max_requests: int = 2000,
        max_nodes: int = 100000,
    ) -> None:
        self.cwd = cwd
        self.hostname = hostname
        self.pause = pause
        self.max_pages = max_pages
        self.max_requests = max_requests
        self.max_nodes = max_nodes
        self._requests = 0
        self._nodes = 0
        self._multi_select = False

    def _graphql(
        self, query: str, variables: dict[str, Any], *, write: bool = False
    ) -> dict[str, Any]:
        for attempt in range(2 if not write else 1):
            self._requests += 1
            if self._requests > self.max_requests:
                raise ProjectError(
                    "Projects request budget exhausted; no complete result is available."
                )
            try:
                response = run(
                    ["gh", "api", "graphql", "--hostname", self.hostname, "--input", "-"],
                    cwd=self.cwd,
                    timeout=30,
                    check=False,
                    retries=0,
                    input_text=json.dumps(
                        {"query": query, "variables": variables}, allow_nan=False
                    ),
                )
            except subprocess.TimeoutExpired as exc:
                if not write and attempt == 0:
                    self.pause(1)
                    continue
                raise ProjectError(
                    "Projects request timed out; rerun to read current truth.",
                    unknown_outcome=write,
                ) from exc
            except CommandError as exc:
                raise ProjectError(
                    "Could not execute gh; install/authenticate gh for the target host."
                ) from exc
            diagnostic = (response.stderr or "").lower()
            if response.returncode:
                if any(s in diagnostic for s in ("rate limit", "rate_limit", "secondary rate")):
                    raise ProjectError(
                        "GitHub rate limit reached; wait for the limit to reset before rerunning.",
                        unknown_outcome=write,
                    )
                transient = any(
                    s in diagnostic
                    for s in (
                        "http 502",
                        "http 503",
                        "http 504",
                        "connection reset",
                        "connection refused",
                    )
                )
                if transient and not write and attempt == 0:
                    self.pause(1)
                    continue
                denied = any(
                    s in diagnostic
                    for s in ("http 401", "http 403", "authentication", "permission", "scope")
                )
                if denied:
                    access = (
                        "project write" if write else "project read and underlying repository read"
                    )
                    raise ProjectError(
                        f"GitHub denied {access} access on {self.hostname}; "
                        "check gh authentication and token/app permissions."
                    )
                raise ProjectError(
                    "GitHub Projects request failed; check host, connectivity and API support.",
                    unknown_outcome=write,
                )
            try:
                payload = _object(json.loads(response.stdout), "GraphQL envelope")
            except (ValueError, ProjectError) as exc:
                raise ProjectError(
                    "GitHub returned malformed Projects JSON.", unknown_outcome=write
                ) from exc
            if "errors" in payload and not isinstance(payload["errors"], list):
                raise ProjectError("Malformed GraphQL errors envelope.", unknown_outcome=write)
            if payload.get("errors"):
                errors = payload["errors"]
                denied = isinstance(errors, list) and any(
                    isinstance(e, dict) and e.get("type") in {"FORBIDDEN", "UNAUTHORIZED"}
                    for e in errors
                )
                if denied:
                    raise ProjectError(
                        "GitHub denied Projects access; check read:project/project "
                        "scopes or corresponding app permissions.",
                        unknown_outcome=write,
                    )
                raise ProjectError(
                    (
                        "GitHub reported a Projects GraphQL error; no partial result "
                        "is accepted. Check schema support, permissions and rate limi"
                        "ts."
                    ),
                    unknown_outcome=write,
                )
            try:
                return _object(payload.get("data"), "GraphQL data")
            except ProjectError as exc:
                raise ProjectError(str(exc), unknown_outcome=write) from exc
        raise AssertionError("bounded retry exhausted")

    def _connection(
        self, fetch: Callable[[str | None], Any], *, key: str = "id"
    ) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        cursor: str | None = None
        cursors: set[str] = set()
        identities: set[str] = set()
        for _ in range(self.max_pages):
            connection = _object(fetch(cursor), "connection")
            raw = connection.get("nodes")
            page = _object(connection.get("pageInfo"), "pageInfo")
            if (
                not isinstance(raw, list)
                or len(raw) > 100
                or type(page.get("hasNextPage")) is not bool
            ):
                raise ProjectError(
                    "Malformed Projects pagination; completeness cannot be established."
                )
            for value in raw:
                node = _object(value, "connection node")
                identity = _text(node.get(key), f"node {key}")
                if identity in identities:
                    raise ProjectError(
                        "Duplicate Projects connection identity; refusing an inconsistent snapshot."
                    )
                identities.add(identity)
                self._nodes += 1
                if self._nodes > self.max_nodes:
                    raise ProjectError(
                        "Projects node budget exhausted; refusing a partial snapshot."
                    )
                nodes.append(node)
            if not page["hasNextPage"]:
                return nodes
            next_cursor = _text(page.get("endCursor"), "next cursor")
            if next_cursor in cursors or not raw:
                raise ProjectError(
                    "Projects pagination did not advance; refusing a partial snapshot."
                )
            cursors.add(next_cursor)
            cursor = next_cursor
        raise ProjectError("Projects page budget exhausted; refusing a partial snapshot.")

    def schema(self, target: ProjectTarget, *, multi_select: bool = False) -> ProjectSchema:
        self._multi_select = multi_select
        fields = _FIELDS
        if multi_select:
            fields = fields.replace(
                "nodes {",
                "nodes { ... on ProjectV2MultiSelectField { multiSelectOptions { id name } }",
                1,
            )
        query = (
            "query($owner:String!,$number:Int!){"
            + target.kind
            + "(login:$owner){projectV2(number:$number){id title url viewerCanUpdate}}}"
        )
        data = self._graphql(query, {"owner": target.owner, "number": target.number})
        owner = _object(data.get(target.kind), "project owner (verify owner kind/login)")
        project = _object(owner.get("projectV2"), "project (verify number and read permission)")
        if type(project.get("viewerCanUpdate")) is not bool:
            raise ProjectError(
                "Projects schema lacks viewerCanUpdate; update the target GitHub server."
            )
        descriptor = ProjectDescriptor(
            id=_text(project.get("id"), "project id"),
            owner=target.owner,
            kind=target.kind,
            number=target.number,
            hostname=self.hostname,
            title=_text(project.get("title"), "project title"),
            url=_text(project.get("url"), "project URL"),
            viewer_can_update=project["viewerCanUpdate"],
        )

        def fetch(cursor: str | None) -> Any:
            data = self._graphql(
                (
                    "query($id:ID!,$after:String){node(id:$id){... on ProjectV2 {id f"
                    "ields(first:100,after:$after){"
                )
                + fields
                + "}}}}",
                {"id": descriptor.id, "after": cursor},
            )
            node = _object(data.get("node"), "project")
            if node.get("id") != descriptor.id:
                raise ProjectError("Project identity changed while reading schema.")
            return node.get("fields")

        parsed: list[ProjectField] = []
        for raw in self._connection(fetch):
            if type(raw.get("isIssueField")) is not bool:
                raise ProjectError(
                    "Projects schema lacks valid isIssueField ownership metadata; "
                    "update the target GitHub server."
                )
            raw_options = raw.get("options", raw.get("multiSelectOptions", []))
            if not isinstance(raw_options, list):
                raise ProjectError("Malformed Projects field options.")
            options = [
                ProjectOption(
                    id=_text(_object(o, "option").get("id"), "option id"),
                    name=_text(o.get("name"), "option name"),
                )
                for o in raw_options
            ]
            if len({o.id for o in options}) != len(options):
                raise ProjectError("Duplicate Projects option IDs.")
            parsed.append(
                ProjectField(
                    id=raw["id"],
                    name=_text(raw.get("name"), "field name"),
                    type=_text(raw.get("dataType"), "field type"),
                    is_issue_field=raw["isIssueField"],
                    options=options,
                )
            )
        return ProjectSchema(project=descriptor, fields=parsed)

    def _content_fragment(self, labels: bool, assignees: bool) -> str:
        label_query = "labels(first:100){nodes{id name} " + _PAGE + "}" if labels else ""
        assignee_query = "assignees(first:100){nodes{id login} " + _PAGE + "}" if assignees else ""
        common = (
            "id number title url state repository{nameWithOwner} " + label_query + assignee_query
        )
        return (
            "__typename ... on Issue {"
            + common
            + "} ... on PullRequest {"
            + common
            + "} ... on DraftIssue {id title "
            + assignee_query
            + "}"
        )

    def _people_or_labels(self, content: dict[str, Any], connection: str, scalar: str) -> list[str]:
        first = content.get(connection)
        content_id = _text(content.get("id"), "content id")
        kind = _text(content.get("__typename"), "content type")

        def fetch(cursor: str | None) -> Any:
            if cursor is None:
                return first
            query = (
                "query($id:ID!,$after:String){node(id:$id){... on "
                + kind
                + " {id "
                + connection
                + "(first:100,after:$after){nodes{id "
                + scalar
                + "} "
                + _PAGE
                + "}}}}"
            )
            node = _object(
                self._graphql(query, {"id": content_id, "after": cursor}).get("node"), "content"
            )
            if node.get("id") != content_id:
                raise ProjectError("Content identity changed while reading a nested connection.")
            return node.get(connection)

        return sorted(_text(n.get(scalar), scalar) for n in self._connection(fetch))

    def _values(self, item_id: str, required: set[str]) -> dict[str, ProjectValue]:
        if not required:
            return {}
        fragment = _VALUES
        if self._multi_select:
            fragment = fragment.replace(
                "nodes {",
                (
                    "nodes { ... on ProjectV2ItemFieldMultiSelectValue { field { ... "
                    "on ProjectV2FieldCommon { id } } options { id name } }"
                ),
                1,
            )

        def fetch(cursor: str | None) -> Any:
            node = _object(
                self._graphql(
                    (
                        "query($id:ID!,$after:String){node(id:$id){... on ProjectV2It"
                        "em {id fieldValues(first:100,after:$after){"
                    )
                    + fragment
                    + "}}}}",
                    {"id": item_id, "after": cursor},
                ).get("node"),
                "project item",
            )
            if node.get("id") != item_id:
                raise ProjectError("Project item identity changed while reading field values.")
            connection = _object(node.get("fieldValues"), "field values")
            raw_nodes = connection.get("nodes")
            if isinstance(raw_nodes, list):
                for value in raw_nodes:
                    value = _object(value, "field value")
                    value["_field_id"] = _text(
                        _object(
                            value.get("field"), "field value identity (unsupported union/schema)"
                        ).get("id"),
                        "value field id",
                    )
            return connection

        values: dict[str, ProjectValue] = {}
        for raw in self._connection(fetch, key="_field_id"):
            field_id = raw["_field_id"]
            if field_id not in required:
                continue
            kind = _text(raw.get("__typename"), "field value type")
            value: str | float | list[str] | None = None
            option_ids: list[str] = []
            available = True
            if kind.endswith("NumberValue"):
                number = raw.get("number")
                if (
                    isinstance(number, bool)
                    or not isinstance(number, (float, int))
                    or not math.isfinite(number)
                ):
                    raise ProjectError("Invalid/nonfinite Projects numeric value.")
                value = float(number)
            elif kind.endswith("DateValue"):
                value = _text(raw.get("date"), "date")
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError("noncanonical date")
                except ValueError as exc:
                    raise ProjectError("Invalid Projects ISO date.") from exc
            elif kind.endswith("TextValue"):
                value = raw.get("text")
                if not isinstance(value, str):
                    raise ProjectError("Invalid Projects text value.")
            elif kind.endswith("SingleSelectValue"):
                option_ids = [_text(raw.get("optionId"), "selected option id")]
                value = _text(raw.get("name"), "selected option name")
            elif kind.endswith("MultiSelectValue"):
                options = raw.get("options")
                if not isinstance(options, list):
                    raise ProjectError("Invalid Projects multiselect options.")
                option_ids = [
                    _text(_object(o, "selected option").get("id"), "selected option id")
                    for o in options
                ]
                value = sorted(_text(o.get("name"), "selected option name") for o in options)
                option_ids.sort()
                if len(set(option_ids)) != len(option_ids):
                    raise ProjectError("Duplicate selected Projects options.")
            elif kind.endswith("IterationValue"):
                value = _text(raw.get("title"), "iteration title")
            elif kind.endswith("RepositoryValue"):
                repository = raw.get("repository")
                value = (
                    _text(_object(repository, "repository").get("nameWithOwner"), "repository name")
                    if repository is not None
                    else None
                )
            elif kind.endswith("MilestoneValue"):
                milestone = raw.get("milestone")
                value = (
                    _text(_object(milestone, "milestone").get("title"), "milestone title")
                    if milestone is not None
                    else None
                )
            elif kind.endswith(("LabelValue", "UserValue")):
                # Complete underlying Issue/PR/Draft connections are read separately.
                pass
            else:
                available = False
            values[field_id] = ProjectValue(
                field_id=field_id,
                type=kind,
                value=value,
                option_ids=option_ids,
                available=available,
            )
        return values

    def _parse_item(
        self,
        raw: dict[str, Any],
        project_id: str,
        required: set[str],
        labels: bool,
        assignees: bool,
    ) -> ProjectItem:
        item_id = _text(raw.get("id"), "item id")
        if (
            _object(raw.get("project"), "item membership").get("id") != project_id
            or type(raw.get("isArchived")) is not bool
        ):
            raise ProjectError("Invalid Projects membership/archive state.")
        content = raw.get("content")
        values = self._values(item_id, required)
        if content is None:
            return ProjectItem(
                id=item_id,
                project_id=project_id,
                kind="Unavailable",
                archived=raw["isArchived"],
                available=False,
                values=values,
            )
        content = _object(content, "item content")
        kind = content.get("__typename")
        if kind not in {"Issue", "PullRequest", "DraftIssue"}:
            raise ProjectError("Unsupported Projects item content union.")
        repository: str | None = None
        number: int | None = None
        url: str | None = None
        state: str | None = None
        if kind != "DraftIssue":
            repository = _text(
                _object(content.get("repository"), "repository").get("nameWithOwner"),
                "repository identity",
            )
            number = content.get("number")
            if type(number) is not int or number <= 0:
                raise ProjectError("Invalid Projects issue/PR number.")
            url = _text(content.get("url"), "content URL")
            state = _text(content.get("state"), "content state")
        item = ProjectItem(
            id=item_id,
            project_id=project_id,
            kind=kind,
            content_id=_text(content.get("id"), "content id"),
            repository=repository,
            number=number,
            title=_text(content.get("title"), "content title"),
            url=url,
            state=state,
            archived=raw["isArchived"],
            values=values,
            labels_complete=labels,
            assignees_complete=assignees,
            labels=self._people_or_labels(content, "labels", "name")
            if labels and kind != "DraftIssue"
            else [],
            assignees=self._people_or_labels(content, "assignees", "login") if assignees else [],
        )
        return item

    def snapshot(
        self,
        schema: ProjectSchema,
        required: set[str],
        *,
        labels: bool = False,
        assignees: bool = False,
    ) -> ProjectSnapshot:
        fragment = (
            "id isArchived project{id} content{" + self._content_fragment(labels, assignees) + "}"
        )

        def fetch(cursor: str | None) -> Any:
            query = (
                (
                    "query($id:ID!,$after:String){node(id:$id){... on ProjectV2 {id i"
                    "tems(first:100,after:$after,archivedStates:[ARCHIVED,NOT_ARCHIVE"
                    "D]){nodes{"
                )
                + fragment
                + "} "
                + _PAGE
                + "}}}}"
            )
            node = _object(
                self._graphql(query, {"id": schema.project.id, "after": cursor}).get("node"),
                "project",
            )
            if node.get("id") != schema.project.id:
                raise ProjectError("Project identity changed while reading items.")
            return node.get("items")

        items = [
            self._parse_item(raw, schema.project.id, required, labels, assignees)
            for raw in self._connection(fetch)
        ]
        return ProjectSnapshot(project=schema.project, fields=schema.fields, items=items)

    def item(
        self,
        project_id: str,
        item_id: str,
        required: set[str],
        *,
        labels: bool = False,
        assignees: bool = False,
    ) -> ProjectItem:
        fragment = (
            "id isArchived project{id} content{" + self._content_fragment(labels, assignees) + "}"
        )
        node = _object(
            self._graphql(
                "query($id:ID!){node(id:$id){... on ProjectV2Item {" + fragment + "}}}",
                {"id": item_id},
            ).get("node"),
            "project item",
        )
        if node.get("id") != item_id:
            raise ProjectError("Project item no longer exists or is inaccessible.")
        return self._parse_item(node, project_id, required, labels, assignees)

    def update_number(self, project_id: str, item_id: str, field_id: str, number: float) -> None:
        if isinstance(number, bool) or not math.isfinite(number):
            raise ProjectError("Numeric Projects mutation requires a finite number.")
        self.pause(0.1)
        data = self._graphql(
            (
                "mutation($project:ID!,$item:ID!,$field:ID!,$number:Float!){updatePro"
                "jectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:"
                "$field,value:{number:$number}}){projectV2Item{id}}}"
            ),
            {"project": project_id, "item": item_id, "field": field_id, "number": number},
            write=True,
        )
        mutation = data.get("updateProjectV2ItemFieldValue")
        if (
            not isinstance(mutation, dict)
            or not isinstance(mutation.get("projectV2Item"), dict)
            or mutation["projectV2Item"].get("id") != item_id
        ):
            raise ProjectError(
                "Projects mutation returned an unexpected item identity; reread before retrying.",
                unknown_outcome=True,
            )
