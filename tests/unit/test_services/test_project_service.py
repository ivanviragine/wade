"""Hard-coded ranking, complete WIP and guarded numeric write contracts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from wade.models.config import ProjectConfig
from wade.models.project import (
    GitHubProjectsConfig,
    ProjectDescriptor,
    ProjectError,
    ProjectField,
    ProjectFieldRef,
    ProjectItem,
    ProjectOption,
    ProjectRequest,
    ProjectSchema,
    ProjectSnapshot,
    ProjectSort,
    ProjectValue,
)
from wade.providers.github_projects import GitHubProjectsProvider
from wade.services.project_service import bind, evaluate, execute


def settings() -> GitHubProjectsConfig:
    return GitHubProjectsConfig.model_validate(
        {
            "target": {"owner": "org", "kind": "organization", "number": 1},
            "fields": {
                "effort": {"id": "E", "type": "NUMBER"},
                "priority": {
                    "id": "P",
                    "type": "SINGLE_SELECT",
                    "options": {"urgent": {"id": "HIGH", "name": "High"}},
                    "option_order": ["HIGH", "LOW"],
                },
                "status": {"id": "S", "type": "SINGLE_SELECT"},
                "score_out": {"id": "O", "type": "NUMBER"},
                "rank_out": {"id": "R", "type": "NUMBER"},
                "due": {"id": "D", "type": "DATE"},
                "tags": {"id": "T", "type": "MULTI_SELECT"},
            },
            "columns": ["repository", "number", "title", "effort"],
            "ranking": {
                "factors": [
                    {
                        "field": "effort",
                        "weight": 2,
                        "minimum": -10,
                        "maximum": 10,
                        "lower_is_better": True,
                        "missing": {"policy": "exclude"},
                    },
                    {
                        "field": "priority",
                        "weight": 1,
                        "options": {"urgent": 10, "LOW": -10},
                        "missing": {"policy": "value", "normalized": 0.5},
                    },
                ]
            },
            "wip": {
                "status": "status",
                "ready": ["Ready"],
                "in_progress": ["Doing"],
                "pool": "project",
                "limit": 3,
            },
            "outputs": {"score": "score_out", "rank": "rank_out"},
        }
    )


def schema() -> ProjectSchema:
    return ProjectSchema(
        project=ProjectDescriptor(
            id="BOARD",
            owner="org",
            kind="organization",
            number=1,
            hostname="github.com",
            title="Board",
            url="https://github.com/orgs/org/projects/1",
            viewer_can_update=True,
        ),
        fields=[
            ProjectField(id="E", name="Effort", type="NUMBER"),
            ProjectField(
                id="P",
                name="Priority",
                type="SINGLE_SELECT",
                options=[
                    ProjectOption(id="HIGH", name="High"),
                    ProjectOption(id="LOW", name="Low"),
                ],
            ),
            ProjectField(
                id="S",
                name="Status",
                type="SINGLE_SELECT",
                options=[
                    ProjectOption(id="READY", name="Ready"),
                    ProjectOption(id="DOING", name="Doing"),
                    ProjectOption(id="DONE", name="Done"),
                ],
            ),
            ProjectField(id="O", name="Score", type="NUMBER", is_issue_field=False),
            ProjectField(id="R", name="Rank", type="NUMBER", is_issue_field=False),
            ProjectField(id="D", name="Due", type="DATE"),
            ProjectField(
                id="T",
                name="Tags",
                type="MULTI_SELECT",
                options=[ProjectOption(id="TAG", name="Tag")],
            ),
        ],
    )


def item(
    number: int,
    effort: float | None = 0,
    priority: str | None = "HIGH",
    status: str | None = "READY",
    **updates: object,
) -> ProjectItem:
    values: dict[str, ProjectValue] = {}
    if effort is not None:
        values["E"] = ProjectValue(
            field_id="E", type="ProjectV2ItemFieldNumberValue", value=float(effort)
        )
    for field_id, option, names in (
        ("P", priority, {"HIGH": "High", "LOW": "Low"}),
        ("S", status, {"READY": "Ready", "DOING": "Doing", "DONE": "Done"}),
    ):
        if option is not None:
            values[field_id] = ProjectValue(
                field_id=field_id,
                type="ProjectV2ItemFieldSingleSelectValue",
                value=names[option],
                option_ids=[option],
            )
    result = ProjectItem(
        id=f"I{number}",
        project_id="BOARD",
        kind="Issue",
        content_id=f"C{number}",
        repository="org/repo",
        number=number,
        title=f"Task {number}",
        url=f"https://github.com/org/repo/issues/{number}",
        state="OPEN",
        labels=["bug"],
        assignees=["dev"],
        labels_complete=True,
        assignees_complete=True,
        values=values,
    )
    return result.model_copy(update=updates)


def evaluate_items(
    items: list[ProjectItem], request: ProjectRequest, config: GitHubProjectsConfig | None = None
):
    config = config or settings()
    board = schema()
    return evaluate(
        config,
        request,
        ProjectSnapshot(project=board.project, fields=board.fields, items=items),
        bind(config, board),
    )


def test_scores_fixed_bounds_missing_clamping_and_api_order() -> None:
    items = [item(3, 99, "HIGH"), item(2, -99, "LOW"), item(1, 0, None), item(4, None)]
    result = evaluate_items(items, ProjectRequest(operation="rank"))
    assert [(row.item.number, row.score, row.rank) for row in result.rows] == [
        (2, 66.666667, 1),
        (1, 50, 2),
        (3, 33.333333, 3),
    ]
    assert result.rows[1].contributions[1].used_missing_value
    assert result.rows[1].contributions[1].normalized == 0.5
    assert result.rows[2].contributions[0].normalized == 0
    assert result.rows[2].contributions[1].weighted == 1
    assert result.excluded[0].item_id == "I4"
    assert (
        result.model_dump()
        == evaluate_items(list(reversed(items)), ProjectRequest(operation="rank")).model_dump()
    )


def test_six_decimal_half_even_and_sort_on_reported_score() -> None:
    config = settings()
    config.ranking.factors = [
        config.ranking.factors[0].model_copy(
            update={"minimum": 0.0, "maximum": 100.0, "lower_is_better": False, "weight": 1.0}
        )
    ]
    result = evaluate_items(
        [item(2, 1.0000005), item(1, 1.0000004), item(3, 1.0000015)],
        ProjectRequest(operation="rank"),
        config,
    )
    assert [(r.item.number, r.score) for r in result.rows] == [(3, 1.000002), (1, 1.0), (2, 1.0)]


@pytest.mark.parametrize("direction,expected", [("asc", [2, 1, 3]), ("desc", [1, 2, 3])])
def test_numeric_sort_null_last_both_directions(direction: str, expected: list[int]) -> None:
    result = evaluate_items(
        [item(3, None), item(1, 10), item(2, -2)],
        ProjectRequest.model_validate(
            {"operation": "items", "sort": [{"field": "effort", "direction": direction}]}
        ),
    )
    assert [r.item.number for r in result.rows] == expected


def test_option_order_date_text_and_qualified_ties() -> None:
    result = evaluate_items(
        [item(1, priority="LOW"), item(2, priority="HIGH"), item(3, priority=None)],
        ProjectRequest(operation="items", sort=[ProjectSort(field="priority")]),
    )
    assert [r.item.number for r in result.rows] == [2, 1, 3]
    a, b = item(1, title="a"), item(1, title="Z", repository="another/repo", id="OTHER")
    assert [
        r.item.title
        for r in evaluate_items(
            [a, b], ProjectRequest(operation="items", sort=[ProjectSort(field="title")])
        ).rows
    ] == ["Z", "a"]
    a.values["D"] = ProjectValue(
        field_id="D", type="ProjectV2ItemFieldDateValue", value="2026-01-01"
    )
    b.values["D"] = ProjectValue(
        field_id="D", type="ProjectV2ItemFieldDateValue", value="2025-12-31"
    )
    assert [
        r.item.id
        for r in evaluate_items(
            [a, b], ProjectRequest(operation="items", sort=[ProjectSort(field="due")])
        ).rows
    ] == ["OTHER", "I1"]
    assert [r.item.id for r in evaluate_items([a, b], ProjectRequest(operation="items")).rows] == [
        "OTHER",
        "I1",
    ]


def test_filters_exact_option_alias_id_name_and_multivalue_membership() -> None:
    a = item(1)
    a.values["T"] = ProjectValue(
        field_id="T", type="ProjectV2ItemFieldMultiSelectValue", value=["Tag"], option_ids=["TAG"]
    )
    for token in ("HIGH", "High", "urgent"):
        result = evaluate_items(
            [a, item(2, priority="LOW")],
            ProjectRequest(
                operation="items",
                filters=[f"priority={token}", "tags=Tag", "labels=bug", "assignees=dev"],
            ),
        )
        assert [r.item.number for r in result.rows] == [1]
    for expression in ("priority=unknown", "effort=nan", "due=2026-1-1", "bad=x", "effort>1"):
        with pytest.raises(ProjectError):
            evaluate_items([], ProjectRequest(operation="items", filters=[expression]))
    with pytest.raises(ProjectError, match="Scalar sorting"):
        evaluate_items([], ProjectRequest(operation="items", sort=[ProjectSort(field="tags")]))


def test_wip_counts_all_kinds_repositories_before_candidate_filters() -> None:
    items = [
        item(1, effort=-10),
        item(2, status="DOING", repository="other/repo", kind="PullRequest"),
        item(3, status="DOING", kind="DraftIssue", repository=None),
        item(4, status="DOING", archived=True),
        item(5, state="CLOSED"),
        item(6, status="DONE"),
    ]
    result = evaluate_items(
        items,
        ProjectRequest(operation="pick", repository="org/repo", label=["bug"], count=5, limit=1),
    )
    assert (result.current_wip, result.available_slots) == (2, 1)
    assert [r.item.number for r in result.rows] == [1]
    config = settings()
    config.wip.limit = 0
    result = evaluate_items(items, ProjectRequest(operation="pick", repository="org/repo"), config)
    assert (
        result.rows == [] and result.available_slots == 0 and result.reason == "WIP pool is full."
    )


def test_assignee_wip_and_missing_or_inaccessible_pool_fail_closed() -> None:
    config = settings()
    config.wip.pool = "assignee"
    with pytest.raises(ProjectError, match="requires --assignee"):
        evaluate_items([], ProjectRequest(operation="pick"), config)
    result = evaluate_items(
        [item(1), item(2, status="DOING", assignees=["other"]), item(3, status="DOING")],
        ProjectRequest(operation="pick", assignee="dev"),
        config,
    )
    assert result.current_wip == 1
    for unavailable in (
        item(2, status=None),
        item(2, available=False),
        item(2, assignees_complete=False),
    ):
        with pytest.raises(ProjectError):
            evaluate_items([unavailable], ProjectRequest(operation="pick", assignee="dev"), config)
    with pytest.raises(ProjectError, match="complete ranking"):
        evaluate_items([item(1, available=False)], ProjectRequest(operation="rank"))


def test_field_and_option_identity_mismatches_and_duplicate_names() -> None:
    config, board = settings(), schema()
    config.fields["effort"].name = "Renamed"
    with pytest.raises(ProjectError, match="mismatch"):
        bind(config, board)
    config = settings()
    config.fields["effort"].id = None
    config.fields["effort"].name = "Effort"
    board.fields.append(ProjectField(id="E2", name="Effort", type="NUMBER"))
    with pytest.raises(ProjectError, match="ambiguous"):
        bind(config, board)
    config = settings()
    config.fields["priority"].options["urgent"].name = "Renamed"
    with pytest.raises(ProjectError, match="mismatch"):
        bind(config, schema())
    config = settings()
    config.wip.ready = ["DOING"]
    with pytest.raises(ProjectError, match="disjoint"):
        bind(config, schema())


def test_sync_aliases_cannot_hide_output_input_feedback_loops() -> None:
    config = settings()
    config.fields["score_out"].id = "E"
    with pytest.raises(ProjectError, match="resolved field ID"):
        evaluate_items([item(1)], ProjectRequest(operation="sync"), config)
    with pytest.raises(ProjectError, match="resolved field ID"):
        evaluate_items([item(1)], ProjectRequest(operation="sync", filters=["score_out=1"]))


class FakeProvider(GitHubProjectsProvider):
    def __init__(self, items: list[ProjectItem]) -> None:
        super().__init__(Path("."), "github.com", pause=lambda _: None)
        self.board = schema()
        self.items = {item.id: item.model_copy(deep=True) for item in items}
        self.writes: list[tuple[str, str, float]] = []
        self.snapshots = 0
        self.drift_snapshot = False
        self.drift_item = False
        self.fail_at: int | None = None
        self.verify_bad = False
        self.error_unknown = True

    def schema(self, target, *, multi_select=False):
        return self.board.model_copy(deep=True)

    def snapshot(self, schema, required, *, labels=False, assignees=False):
        self.snapshots += 1
        if self.drift_snapshot and self.snapshots == 2:
            self.items["I1"].title = "Concurrent edit"
        return ProjectSnapshot(
            project=self.board.project.model_copy(),
            fields=self.board.fields,
            items=[item.model_copy(deep=True) for item in self.items.values()],
        )

    def item(self, project_id, item_id, required, *, labels=False, assignees=False):
        result = self.items[item_id].model_copy(deep=True)
        if self.drift_item and len(self.writes) == 1 and item_id == "I2":
            result.title = "Concurrent edit"
        if self.verify_bad and self.writes:
            result.title = "Unverified"
        return result

    def update_number(self, project_id, item_id, field_id, number):
        if self.fail_at == len(self.writes):
            raise ProjectError("Network outcome unknown", unknown_outcome=self.error_unknown)
        self.writes.append((item_id, field_id, number))
        self.items[item_id].values[field_id] = ProjectValue(
            field_id=field_id, type="ProjectV2ItemFieldNumberValue", value=number
        )


def run_sync(provider: FakeProvider, **options: object):
    config = ProjectConfig(github_projects=settings())
    with patch("wade.services.check_service._github_hostname", return_value="github.com"):
        return execute(
            ProjectRequest.model_validate({"operation": "sync", **options}),
            provider=provider,
            project_config=config,
        )


def test_sync_default_dry_run_ranks_before_display_limit_and_idempotent_apply() -> None:
    provider = FakeProvider([item(2, 10), item(1, -10)])
    preview = run_sync(provider, limit=1)
    assert len(preview.rows) == 1 and len(preview.operations) == 4 and provider.writes == []
    assert [(o.identity, o.field, o.new) for o in preview.operations] == [
        ("org/repo:Issue:1", "score_out", 100),
        ("org/repo:Issue:1", "rank_out", 1),
        ("org/repo:Issue:2", "score_out", 33.333333),
        ("org/repo:Issue:2", "rank_out", 2),
    ]
    applied = run_sync(provider, apply=True)
    assert applied.success and all(o.status == "confirmed" for o in applied.operations)
    assert len(provider.writes) == 4
    again = run_sync(provider, apply=True)
    assert again.success and all(o.status == "noop" for o in again.operations)
    assert len(provider.writes) == 4


def test_sync_drift_before_first_write_sends_zero_mutations() -> None:
    provider = FakeProvider([item(1)])
    provider.drift_snapshot = True
    result = run_sync(provider, apply=True)
    assert not result.success and provider.writes == []
    assert all(o.status == "not_attempted" for o in result.operations)


def test_sync_partial_unknown_and_fresh_reconciliation() -> None:
    provider = FakeProvider([item(1), item(2)])
    provider.fail_at = 1
    result = run_sync(provider, apply=True)
    assert not result.success
    assert [o.status for o in result.operations] == [
        "confirmed",
        "unknown",
        "not_attempted",
        "not_attempted",
    ]
    provider.fail_at = None
    recovered = run_sync(provider, apply=True)
    assert recovered.success and recovered.operations[0].status == "noop"
    assert len(provider.writes) == 4


def test_sync_drift_after_confirmed_success_stops_remaining_writes() -> None:
    provider = FakeProvider([item(1), item(2)])
    provider.drift_item = True
    # Only score output makes the next operation belong to another item.
    config = settings()
    config.outputs.rank = None
    with patch("wade.services.check_service._github_hostname", return_value="github.com"):
        result = execute(
            ProjectRequest(operation="sync", apply=True),
            provider=provider,
            project_config=ProjectConfig(github_projects=config),
        )
    assert [o.status for o in result.operations] == ["confirmed", "failed"]
    assert len(provider.writes) == 1 and not result.success


def test_sync_write_verification_failure_and_read_only_permission() -> None:
    provider = FakeProvider([item(1)])
    provider.verify_bad = True
    result = run_sync(provider, apply=True)
    assert [o.status for o in result.operations] == ["unknown", "not_attempted"]
    assert len(provider.writes) == 1
    provider = FakeProvider([item(1)])
    provider.board.project.viewer_can_update = False
    assert run_sync(provider).success
    with pytest.raises(ProjectError, match="write permission"):
        run_sync(provider, apply=True)
    assert provider.writes == []


def test_non_github_provider_rejected_without_queries() -> None:
    config = ProjectConfig()
    config.provider.name = "markdown"
    with pytest.raises(ProjectError, match=r"provider.name: github"):
        execute(ProjectRequest(operation="items"), project_config=config)


def test_pick_limit_is_presentation_only_and_ranked_tables_include_score() -> None:
    result = evaluate_items(
        [item(1), item(2), item(3)], ProjectRequest(operation="pick", count=3, limit=1)
    )
    assert result.current_wip == 0 and result.available_slots == 3 and len(result.rows) == 1
    assert "score" in result.columns and "rank" in result.columns


def test_permission_denied_write_is_failed_and_readback_error_is_unknown() -> None:
    provider = FakeProvider([item(1)])
    provider.fail_at = 0
    provider.error_unknown = False
    result = run_sync(provider, apply=True)
    assert [o.status for o in result.operations] == ["failed", "not_attempted"]
    assert provider.writes == []
    provider = FakeProvider([item(1)])
    original = provider.item

    def read(*args, **kwargs):
        if provider.writes:
            raise ProjectError("Readback unavailable")
        return original(*args, **kwargs)

    with patch.object(provider, "item", side_effect=read):
        result = run_sync(provider, apply=True)
    assert [o.status for o in result.operations] == ["unknown", "not_attempted"]
    assert len(provider.writes) == 1


def test_unknown_selected_option_or_type_is_not_missing_input() -> None:
    value = item(1)
    value.values["P"].option_ids = ["STALE"]
    with pytest.raises(ProjectError, match="Unknown selected option"):
        evaluate_items([value], ProjectRequest(operation="rank"))
    value = item(1)
    value.values["E"].type = "FutureValue"
    with pytest.raises(ProjectError, match="incompatible value"):
        evaluate_items([value], ProjectRequest(operation="rank"))


@pytest.mark.parametrize(
    "reference",
    [
        {"id": "High"},
        {"id": "High", "name": "High"},
        {"id": "stale", "name": "High"},
        {"name": "HIGH"},
    ],
)
def test_explicit_option_identity_never_falls_back_to_other_identity_type(reference) -> None:
    config = settings()
    from wade.models.project import ProjectOptionRef

    config.fields["priority"].options["urgent"] = ProjectOptionRef.model_validate(reference)
    with pytest.raises(ProjectError, match="missing or ambiguous"):
        bind(config, schema())


def test_explicit_option_names_need_unique_exact_matches_and_ids_remain_authoritative() -> None:
    config, board = settings(), schema()
    from wade.models.project import ProjectOptionRef

    config.fields["priority"].options["urgent"] = ProjectOptionRef(name="High")
    assert bind(config, board).options["priority"]["urgent"] == "HIGH"
    board.fields[1].options.append(ProjectOption(id="OTHER", name="High"))
    with pytest.raises(ProjectError, match="missing or ambiguous"):
        bind(config, board)
    config.fields["priority"].options["urgent"] = ProjectOptionRef(id="HIGH")
    assert bind(config, board).options["priority"]["urgent"] == "HIGH"
    config.fields["priority"].options["LOW"] = ProjectOptionRef(id="HIGH")
    bindings = bind(config, board)
    assert bindings.factor_scores["priority"]["LOW"] == -10
    result = evaluate(
        config,
        ProjectRequest(operation="items", filters=["priority=LOW"]),
        ProjectSnapshot(
            project=board.project, fields=board.fields, items=[item(1), item(2, priority="LOW")]
        ),
        bindings,
    )
    assert [row.item.number for row in result.rows] == [2]


@pytest.mark.parametrize("ownership", [True, None])
@pytest.mark.parametrize("apply", [False, True])
def test_issue_backed_or_unknown_outputs_refuse_before_any_write(ownership, apply) -> None:
    provider = FakeProvider([item(1)])
    provider.board.fields = [
        field.model_copy(update={"is_issue_field": ownership}) if field.id == "R" else field
        for field in provider.board.fields
    ]
    with pytest.raises(ProjectError, match="project-owned"):
        run_sync(provider, apply=apply)
    assert provider.writes == []


@pytest.mark.parametrize(
    "remote", ["https://github.com/org/repo.git", "git@github.com:ORG/repo.git"]
)
def test_default_pick_matches_current_repository_identity_without_case_loss(remote: str) -> None:
    provider = FakeProvider(
        [item(1, repository="Org/Repo"), item(2, repository="other/repo", status="DOING")]
    )
    with (
        patch("wade.services.project_service.repo.get_remote_url", return_value=remote),
        patch("wade.services.check_service._github_hostname", return_value="github.com"),
    ):
        result = execute(
            ProjectRequest(operation="pick"),
            project_config=ProjectConfig(github_projects=settings()),
            provider=provider,
        )
    assert [row.item.repository for row in result.rows] == ["Org/Repo"]
    assert result.current_wip == 1 and result.available_slots == 2 and provider.writes == []


@pytest.mark.parametrize(
    "options", [{"repository": "ORG/REPO"}, {"filters": ["repository=ORG/REPO"]}]
)
def test_repository_filters_follow_github_identity_case_rules(options) -> None:
    result = evaluate_items(
        [item(1, repository="Org/Repo")],
        ProjectRequest.model_validate({"operation": "pick", **options}),
    )
    assert [row.item.repository for row in result.rows] == ["Org/Repo"]
    assert (
        evaluate_items(
            [item(1, title="Task")], ProjectRequest(operation="items", filters=["title=task"])
        ).rows
        == []
    )


@pytest.mark.parametrize("alias", ["repository", "repo_alias"])
def test_pick_explicit_repository_filter_never_injects_origin(alias: str) -> None:
    config = settings()
    provider = FakeProvider([item(1, repository="other/repo"), item(2, status="DOING")])
    if alias == "repo_alias":
        config.fields[alias] = ProjectFieldRef(id="REPO", type="REPOSITORY")
        provider.board.fields.append(ProjectField(id="REPO", name="Repository", type="REPOSITORY"))
    with (
        patch(
            "wade.services.project_service.repo.get_remote_url",
            side_effect=AssertionError("Explicit repository filter must not infer origin"),
        ),
        patch("wade.services.check_service._github_hostname", return_value="github.com"),
    ):
        result = execute(
            ProjectRequest(operation="pick", filters=[f"{alias}=other/repo"]),
            project_config=ProjectConfig(github_projects=config),
            provider=provider,
        )
    assert [row.item.repository for row in result.rows] == ["other/repo"]
    assert result.current_wip == 1 and result.available_slots == 2 and provider.writes == []


@pytest.mark.parametrize("login", ["Dev", "dev", "DEV"])
def test_assignee_identity_case_preserves_wip_capacity_and_selection(login: str) -> None:
    config = settings()
    config.wip.pool = "assignee"
    config.wip.limit = 2
    result = evaluate_items(
        [item(1, assignees=["Dev"]), item(2, status="DOING", assignees=["Dev"])],
        ProjectRequest(operation="pick", repository="org/repo", assignee=login),
        config,
    )
    assert result.current_wip == 1 and result.available_slots == 1
    assert [row.item.number for row in result.rows] == [1]
    assert result.rows[0].item.assignees == ["Dev"]


@pytest.mark.parametrize("alias", ["assignees", "owners"])
def test_generic_assignee_identity_filters_preserve_api_spelling(alias: str) -> None:
    config = settings()
    provider = FakeProvider(
        [item(1, assignees=["Dev"]), item(2, status="DOING", assignees=["Dev"])]
    )
    if alias == "owners":
        config.fields[alias] = ProjectFieldRef(id="USERS", type="ASSIGNEES")
        provider.board.fields.append(ProjectField(id="USERS", name="Assignees", type="ASSIGNEES"))
    with patch("wade.services.check_service._github_hostname", return_value="github.com"):
        result = execute(
            ProjectRequest(operation="pick", repository="org/repo", filters=[f"{alias}=dev"]),
            project_config=ProjectConfig(github_projects=config),
            provider=provider,
        )
    assert [row.item.assignees for row in result.rows] == [["Dev"]]
    assert result.current_wip == 1 and provider.writes == []
    assert (
        evaluate_items(
            [item(1, labels=["Bug"])], ProjectRequest(operation="items", label=["bug"])
        ).rows
        == []
    )
