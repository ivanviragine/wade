"""Typed Projects v2 configuration and operation results, independent of providers."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


def _numeric(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a finite number, not a boolean or string")
    return value


Number = Annotated[float, BeforeValidator(_numeric), Field(allow_inf_nan=False)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]


class ProjectModel(BaseModel):
    """Reject misspelled nested settings and invalid typed API payloads."""

    model_config = ConfigDict(extra="forbid")


class ProjectFieldType(StrEnum):
    TEXT = "TEXT"
    TITLE = "TITLE"
    NUMBER = "NUMBER"
    DATE = "DATE"
    SINGLE_SELECT = "SINGLE_SELECT"
    MULTI_SELECT = "MULTI_SELECT"
    ITERATION = "ITERATION"
    LABELS = "LABELS"
    ASSIGNEES = "ASSIGNEES"
    REPOSITORY = "REPOSITORY"
    MILESTONE = "MILESTONE"


class ProjectTarget(ProjectModel):
    owner: str = Field(min_length=1)
    kind: Literal["user", "organization"]
    number: PositiveInt
    hostname: str | None = None

    @field_validator("owner")
    @classmethod
    def valid_owner(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", value):
            raise ValueError("owner must be a GitHub login")
        return value

    @field_validator("hostname")
    @classmethod
    def valid_host(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?", value):
            raise ValueError("hostname must be a hostname, without scheme or path")
        return value.lower() if value else value


class ProjectOptionRef(ProjectModel):
    id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def identity_required(self) -> Self:
        if not self.id and not self.name:
            raise ValueError("option requires an id or exact name")
        return self


class ProjectFieldRef(ProjectOptionRef):
    type: ProjectFieldType
    options: dict[str, ProjectOptionRef] = Field(default_factory=dict)
    option_order: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def selectable_options(self) -> Self:
        if (self.options or self.option_order) and self.type not in {
            ProjectFieldType.SINGLE_SELECT,
            ProjectFieldType.MULTI_SELECT,
        }:
            raise ValueError("option references/order require a select field")
        if self.option_order and self.type != ProjectFieldType.SINGLE_SELECT:
            raise ValueError("option_order is supported only for single-select sorting")
        return self

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class ProjectSort(ProjectModel):
    field: str = Field(min_length=1)
    direction: Literal["asc", "desc"] = "asc"


class ProjectMissingPolicy(ProjectModel):
    policy: Literal["exclude", "value"]
    normalized: Annotated[Number, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def explicit_fallback(self) -> Self:
        if (self.policy == "value") != (self.normalized is not None):
            raise ValueError("normalized is required only for missing policy=value")
        return self


class ProjectFactor(ProjectModel):
    field: str = Field(min_length=1)
    weight: Annotated[Number, Field(ge=0)] = 1.0
    minimum: Number | None = None
    maximum: Number | None = None
    lower_is_better: StrictBool = False
    options: dict[str, Number] = Field(default_factory=dict)
    missing: ProjectMissingPolicy

    @model_validator(mode="after")
    def valid_bounds(self) -> Self:
        if (self.minimum is None) != (self.maximum is None):
            raise ValueError("minimum and maximum must be supplied together")
        if self.minimum is not None and self.maximum is not None and self.minimum >= self.maximum:
            raise ValueError("minimum must be less than maximum")
        if self.options and self.minimum is not None:
            raise ValueError("option factors and numeric bounds are mutually exclusive")
        return self


class ProjectRanking(ProjectModel):
    factors: list[ProjectFactor] = Field(default_factory=list)

    @model_validator(mode="after")
    def positive_weight_total(self) -> Self:
        if self.factors and not any(f.weight > 0 for f in self.factors):
            raise ValueError("ranking factors need a positive total weight")
        if len({f.field for f in self.factors}) != len(self.factors):
            raise ValueError("ranking factor fields must be unique")
        return self


class ProjectWip(ProjectModel):
    status: str
    ready: list[str] = Field(min_length=1)
    in_progress: list[str] = Field(min_length=1)
    pool: Literal["project", "assignee"]
    limit: NonnegativeInt


class ProjectOutputs(ProjectModel):
    score: str | None = None
    rank: str | None = None


PROJECT_COLUMNS = frozenset(
    {
        "id",
        "title",
        "url",
        "kind",
        "repository",
        "number",
        "state",
        "labels",
        "assignees",
        "archived",
        "score",
        "rank",
    }
)


class GitHubProjectsConfig(ProjectModel):
    target: ProjectTarget | None = None
    fields: dict[str, ProjectFieldRef] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=lambda: ["repository", "number", "title", "kind"])
    sort: list[ProjectSort] = Field(default_factory=list)
    ranking: ProjectRanking = Field(default_factory=ProjectRanking)
    wip: ProjectWip | None = None
    outputs: ProjectOutputs = Field(default_factory=ProjectOutputs)

    @model_validator(mode="before")
    @classmethod
    def optional_section_shapes(cls, value: object) -> object:
        if isinstance(value, dict):
            for key in ("target", "wip"):
                if key in value and value[key] is None:
                    raise ValueError(f"{key} must be a mapping when present; omit unused settings")
        return value

    @model_validator(mode="after")
    def valid_aliases(self) -> Self:
        for alias in self.fields:
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", alias) or alias in PROJECT_COLUMNS:
                raise ValueError(f"invalid or reserved field alias: {alias}")
        available = PROJECT_COLUMNS | self.fields.keys()
        if not self.columns or len(set(self.columns)) != len(self.columns):
            raise ValueError("columns must be nonempty and unique")
        for alias in [*self.columns, *(s.field for s in self.sort)]:
            if alias not in available:
                raise ValueError(f"unknown column or sort alias: {alias}")
        inputs = {f.field for f in self.ranking.factors}
        if self.wip:
            inputs.add(self.wip.status)
            if (
                self.wip.status not in self.fields
                or self.fields[self.wip.status].type != ProjectFieldType.SINGLE_SELECT
            ):
                raise ValueError("wip.status must map to a single-select field")
        for factor in self.ranking.factors:
            if factor.field not in self.fields:
                raise ValueError(f"unknown ranking field: {factor.field}")
            kind = self.fields[factor.field].type
            if kind == ProjectFieldType.NUMBER:
                if factor.minimum is None or factor.options:
                    raise ValueError("numeric factors require fixed minimum/maximum bounds")
            elif kind == ProjectFieldType.SINGLE_SELECT:
                if not factor.options or factor.minimum is not None:
                    raise ValueError("single-select factors require explicit option scores")
                if len(set(factor.options.values())) < 2:
                    raise ValueError("option factors require at least two distinct scores")
            else:
                raise ValueError(f"unsupported ranking type: {kind}")
        outputs = [a for a in [self.outputs.score, self.outputs.rank] if a is not None]
        if len(set(outputs)) != len(outputs) or set(outputs) & inputs:
            raise ValueError("output fields must differ from each other and ranking/WIP inputs")
        for alias in outputs:
            if alias not in self.fields or self.fields[alias].type != ProjectFieldType.NUMBER:
                raise ValueError("output fields must map to numeric fields")
        return self


class ProjectOption(ProjectModel):
    id: str
    name: str


class ProjectField(ProjectModel):
    id: str
    name: str
    type: str
    is_issue_field: bool | None = Field(default=None, strict=True)
    options: list[ProjectOption] = Field(default_factory=list)


class ProjectDescriptor(ProjectModel):
    id: str
    owner: str
    kind: Literal["user", "organization"]
    number: int
    hostname: str
    title: str
    url: str
    viewer_can_update: bool


class ProjectValue(ProjectModel):
    field_id: str
    type: str
    value: str | Number | list[str] | None = None
    option_ids: list[str] = Field(default_factory=list)
    available: bool = True


class ProjectItem(ProjectModel):
    id: str
    project_id: str
    kind: Literal["Issue", "PullRequest", "DraftIssue", "Unavailable"]
    content_id: str | None = None
    repository: str | None = None
    number: int | None = None
    title: str | None = None
    url: str | None = None
    state: str | None = None
    archived: bool = False
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    available: bool = True
    labels_complete: bool = False
    assignees_complete: bool = False
    values: dict[str, ProjectValue] = Field(default_factory=dict)

    @property
    def identity(self) -> str:
        content = self.number if self.number is not None else self.content_id or self.id
        return f"{self.repository or ''}:{self.kind}:{content}"


class ProjectSnapshot(ProjectModel):
    project: ProjectDescriptor
    fields: list[ProjectField]
    items: list[ProjectItem]
    visibility: str = "Only resources visible to the authenticated GitHub principal are included."


class ProjectContribution(ProjectModel):
    field: str
    field_id: str
    raw: str | Number | None
    normalized: Number
    weight: Number
    weighted: Number
    used_missing_value: bool = False


class ProjectRow(ProjectModel):
    item: ProjectItem
    values: dict[str, str | Number | list[str] | bool | None]
    score: Number | None = None
    rank: int | None = None
    contributions: list[ProjectContribution] = Field(default_factory=list)


class ProjectExclusion(ProjectModel):
    item_id: str
    identity: str
    reason: str


class ProjectSyncOperation(ProjectModel):
    item_id: str
    identity: str
    field_id: str
    field: str
    old: Number | None
    new: Number
    status: Literal["planned", "noop", "confirmed", "failed", "unknown", "not_attempted"] = (
        "planned"
    )
    message: str | None = None


class ProjectResult(ProjectModel):
    operation: Literal["items", "rank", "pick", "sync"]
    project: ProjectDescriptor
    columns: list[str]
    rows: list[ProjectRow]
    excluded: list[ProjectExclusion] = Field(default_factory=list)
    operations: list[ProjectSyncOperation] = Field(default_factory=list)
    visibility: str
    success: bool = True
    reason: str | None = None
    current_wip: int | None = None
    wip_limit: int | None = None
    available_slots: int | None = None
    applied: bool = False


class ProjectSchema(ProjectModel):
    project: ProjectDescriptor
    fields: list[ProjectField]


class ProjectError(Exception):
    """Actionable Projects failure; uncertain writes must never be blindly retried."""

    def __init__(self, message: str, *, unknown_outcome: bool = False) -> None:
        super().__init__(message)
        self.unknown_outcome = unknown_outcome


class ProjectRequest(ProjectModel):
    operation: Literal["items", "rank", "pick", "sync"]
    target: ProjectTarget | None = None
    columns: list[str] | None = None
    sort: list[ProjectSort] | None = None
    filters: list[str] = Field(default_factory=list)
    repository: str | None = None
    label: list[str] = Field(default_factory=list)
    assignee: str | None = None
    status: str | None = None
    kind: Literal["Issue", "PullRequest", "DraftIssue"] | None = None
    include_archived: bool = False
    limit: PositiveInt | None = None
    count: PositiveInt = 1
    apply: bool = False
    dry_run: bool = False

    @model_validator(mode="after")
    def write_intent(self) -> Self:
        if self.apply and self.dry_run:
            raise ValueError("--apply and --dry-run cannot be combined")
        if (self.apply or self.dry_run) and self.operation != "sync":
            raise ValueError("write flags are supported only by sync")
        return self


class ProjectBindings(ProjectModel):
    fields: dict[str, ProjectField]
    options: dict[str, dict[str, str]] = Field(default_factory=dict)
    option_order: dict[str, list[str]] = Field(default_factory=dict)
    factor_scores: dict[str, dict[str, Number]] = Field(default_factory=dict)
    ready: list[str] = Field(default_factory=list)
    in_progress: list[str] = Field(default_factory=list)
