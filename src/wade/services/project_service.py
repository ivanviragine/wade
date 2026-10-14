"""Deterministic Projects listing, scoring, advisory selection and guarded write-back."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from functools import cmp_to_key
from pathlib import Path
from urllib.parse import urlparse

from wade.config.loader import load_config
from wade.git import repo
from wade.models.config import ProjectConfig
from wade.models.project import (
    PROJECT_COLUMNS,
    GitHubProjectsConfig,
    ProjectBindings,
    ProjectContribution,
    ProjectError,
    ProjectExclusion,
    ProjectField,
    ProjectFieldType,
    ProjectItem,
    ProjectOptionRef,
    ProjectRequest,
    ProjectResult,
    ProjectRow,
    ProjectSchema,
    ProjectSnapshot,
    ProjectSort,
    ProjectSyncOperation,
    ProjectValue,
)
from wade.providers.github_projects import GitHubProjectsProvider

_QUANTUM = Decimal("0.000001")


def _resolve_field(reference: ProjectOptionRef, fields: list[ProjectField]) -> ProjectField:
    candidates = (
        [f for f in fields if f.id == reference.id]
        if reference.id
        else [f for f in fields if f.name == reference.name]
    )
    if len(candidates) != 1:
        raise ProjectError(
            "Mapped field ID/name is missing or ambiguous; discover the current project schema."
        )
    field = candidates[0]
    if reference.name is not None and field.name != reference.name:
        raise ProjectError(f"Mapped field ID/name mismatch: {field.id}.")
    return field


def _resolve_option(field: ProjectField, token: str, aliases: dict[str, str]) -> str:
    if token in aliases:
        return aliases[token]
    by_id = [o.id for o in field.options if o.id == token]
    by_name = [o.id for o in field.options if o.name == token]
    candidates = by_id or by_name
    if len(candidates) != 1:
        raise ProjectError(f"Unknown or ambiguous option {token!r} for field {field.name}.")
    return candidates[0]


def bind(config: GitHubProjectsConfig, schema: ProjectSchema) -> ProjectBindings:
    """Resolve configured identities once, without fuzzy names or API-order dependence."""
    bindings = ProjectBindings(fields={})
    for alias, reference in config.fields.items():
        field = _resolve_field(reference, schema.fields)
        if field.type != reference.type:
            raise ProjectError(f"Field {alias} has type {field.type}, expected {reference.type}.")
        bindings.fields[alias] = field
        aliases: dict[str, str] = {}
        for name, option_ref in reference.options.items():
            option_id = _resolve_option(field, option_ref.id or option_ref.name or "", {})
            actual = next(o for o in field.options if o.id == option_id)
            if option_ref.name is not None and actual.name != option_ref.name:
                raise ProjectError(f"Option ID/name mismatch for {alias}.{name}.")
            aliases[name] = option_id
        bindings.options[alias] = aliases
        order = [_resolve_option(field, token, aliases) for token in reference.option_order]
        if len(set(order)) != len(order):
            raise ProjectError(f"Duplicate option order for {alias}.")
        bindings.option_order[alias] = order + [
            o.id for o in sorted(field.options, key=lambda o: (o.name, o.id)) if o.id not in order
        ]
    for factor in config.ranking.factors:
        field = bindings.fields[factor.field]
        scores: dict[str, float] = {}
        for token, score in factor.options.items():
            option_id = _resolve_option(field, token, bindings.options[factor.field])
            if option_id in scores:
                raise ProjectError(f"Duplicate ranking option identity for {factor.field}.")
            scores[option_id] = score
        bindings.factor_scores[factor.field] = scores
    if config.wip:
        alias = config.wip.status
        field = bindings.fields[alias]
        bindings.ready = [
            _resolve_option(field, token, bindings.options[alias]) for token in config.wip.ready
        ]
        bindings.in_progress = [
            _resolve_option(field, token, bindings.options[alias])
            for token in config.wip.in_progress
        ]
        if set(bindings.ready) & set(bindings.in_progress):
            raise ProjectError("WIP ready/in-progress option identities must be disjoint.")
    return bindings


def _value(
    item: ProjectItem, alias: str, bindings: ProjectBindings
) -> str | float | list[str] | bool | None:
    if alias in PROJECT_COLUMNS:
        return {
            "id": item.id,
            "title": item.title,
            "url": item.url,
            "kind": item.kind,
            "repository": item.repository,
            "number": float(item.number) if item.number is not None else None,
            "state": item.state,
            "labels": item.labels if item.labels_complete else None,
            "assignees": item.assignees if item.assignees_complete else None,
            "archived": item.archived,
            "score": None,
            "rank": None,
        }[alias]
    field = bindings.fields[alias]
    if field.type == "TITLE":
        return item.title
    if field.type == "REPOSITORY":
        return item.repository
    if field.type == "LABELS":
        if not item.labels_complete:
            raise ProjectError("Required label connection is incomplete.")
        return item.labels
    if field.type == "ASSIGNEES":
        if not item.assignees_complete:
            raise ProjectError("Required assignee connection is incomplete.")
        return item.assignees
    value = item.values.get(field.id)
    if value is None:
        return None
    expected = {
        "TEXT": "TextValue",
        "NUMBER": "NumberValue",
        "DATE": "DateValue",
        "SINGLE_SELECT": "SingleSelectValue",
        "MULTI_SELECT": "MultiSelectValue",
        "ITERATION": "IterationValue",
        "MILESTONE": "MilestoneValue",
    }.get(field.type)
    if not value.available or expected is None or not value.type.endswith(expected):
        raise ProjectError(
            f"Unavailable or incompatible value for field {alias} on {item.identity}."
        )
    if field.type in {"SINGLE_SELECT", "MULTI_SELECT"}:
        known = {o.id: o.name for o in field.options}
        if any(option not in known for option in value.option_ids):
            raise ProjectError(f"Unknown selected option for {alias}; schema changed.")
        if field.type == "SINGLE_SELECT":
            if len(value.option_ids) != 1 or value.value != known[value.option_ids[0]]:
                raise ProjectError(f"Invalid selected option for {alias}.")
        elif sorted(known[o] for o in value.option_ids) != value.value:
            raise ProjectError(f"Invalid selected options for {alias}.")
    return value.value


def _sorts(
    config: GitHubProjectsConfig, request: ProjectRequest, bindings: ProjectBindings
) -> list[ProjectSort]:
    sorts = request.sort if request.sort is not None else config.sort
    for sort in sorts:
        if sort.field not in PROJECT_COLUMNS and sort.field not in bindings.fields:
            raise ProjectError(f"Unknown sort field: {sort.field}.")
        kind = bindings.fields[sort.field].type if sort.field in bindings.fields else sort.field
        if kind in {"MULTI_SELECT", "LABELS", "ASSIGNEES", "labels", "assignees", "ITERATION"}:
            raise ProjectError(f"Scalar sorting is unsupported for {sort.field}.")
        if request.operation == "items" and sort.field in {"score", "rank"}:
            raise ProjectError("Score/rank sorting requires a ranking operation.")
    return sorts


def _filters(
    config: GitHubProjectsConfig, request: ProjectRequest, bindings: ProjectBindings
) -> list[tuple[str, str | float | bool]]:
    filters: list[tuple[str, str | float | bool]] = []
    raw = list(request.filters)
    for alias, flag_value in (
        ("repository", request.repository),
        ("assignees", request.assignee),
        ("kind", request.kind),
    ):
        if flag_value is not None:
            raw.append(f"{alias}={flag_value}")
    raw.extend(f"labels={label}" for label in request.label)
    if request.status is not None:
        if config.wip is None:
            raise ProjectError("--status requires an explicit wip.status field mapping.")
        raw.append(f"{config.wip.status}={request.status}")
    for expression in raw:
        alias, separator, token = expression.partition("=")
        if (
            not separator
            or not token
            or alias not in PROJECT_COLUMNS | bindings.fields.keys()
            or alias in {"score", "rank"}
        ):
            raise ProjectError(f"Invalid exact filter: {expression!r}; use alias=value.")
        value: str | float | bool = token
        kind = bindings.fields[alias].type if alias in bindings.fields else alias
        if kind in {"NUMBER", "number"}:
            try:
                parsed = Decimal(token)
                if not parsed.is_finite():
                    raise ValueError("nonfinite")
                value = float(parsed)
                if not Decimal(str(value)).is_finite():
                    raise ValueError("out of range")
            except (ValueError, ArithmeticError) as exc:
                raise ProjectError(f"Invalid numeric filter for {alias}.") from exc
        elif kind == "DATE":
            try:
                if date.fromisoformat(token).isoformat() != token:
                    raise ValueError("noncanonical")
            except ValueError as exc:
                raise ProjectError(f"Invalid ISO date filter for {alias}.") from exc
        elif kind in {"SINGLE_SELECT", "MULTI_SELECT"}:
            value = _resolve_option(bindings.fields[alias], token, bindings.options[alias])
        elif alias == "repository" and not re.fullmatch(r"[^/\s]+/[^/\s]+", token):
            raise ProjectError("Repository filter requires qualified owner/repo.")
        elif alias == "assignees" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", token):
            raise ProjectError("Assignee filter requires a GitHub login.")
        elif alias == "archived":
            if token not in {"true", "false"}:
                raise ProjectError("archived filter requires true or false.")
            value = token == "true"
        elif alias == "kind" and token not in {"Issue", "PullRequest", "DraftIssue", "Unavailable"}:
            raise ProjectError(
                "kind filter requires Issue, PullRequest, DraftIssue or Unavailable."
            )
        filters.append((alias, value))
    return filters


def _matches(
    item: ProjectItem, filters: list[tuple[str, str | float | bool]], bindings: ProjectBindings
) -> bool:
    for alias, expected in filters:
        if not item.available and alias not in {"id", "kind", "archived"}:
            raise ProjectError("Unavailable content prevents evaluating a required filter.")
        field = bindings.fields.get(alias)
        if field and field.type in {"SINGLE_SELECT", "MULTI_SELECT"}:
            _value(item, alias, bindings)
            actual = item.values.get(field.id)
            if actual is None or expected not in actual.option_ids:
                return False
        else:
            value = _value(item, alias, bindings)
            if isinstance(value, list):
                if expected not in value:
                    return False
            elif value != expected:
                return False
    return True


def _score(
    item: ProjectItem, config: GitHubProjectsConfig, bindings: ProjectBindings
) -> tuple[float | None, list[ProjectContribution], str | None]:
    contributions: list[ProjectContribution] = []
    total = Decimal(0)
    weights = Decimal(0)
    with localcontext() as context:
        context.prec = 50
        for factor in config.ranking.factors:
            raw = _value(item, factor.field, bindings)
            if isinstance(raw, (list, bool)):
                raise ProjectError("Ranking requires scalar numeric or single-select values.")
            normalized: Decimal
            missing = raw is None
            if missing:
                if factor.missing.policy == "exclude":
                    return None, [], f"Missing ranking input: {factor.field}"
                normalized = Decimal(str(factor.missing.normalized))
            else:
                if factor.options:
                    value = item.values[bindings.fields[factor.field].id]
                    option_id = value.option_ids[0]
                    scores = bindings.factor_scores[factor.field]
                    if option_id not in scores:
                        raise ProjectError(f"Unscored option for ranking factor {factor.field}.")
                    number = Decimal(str(scores[option_id]))
                    minimum, maximum = (
                        Decimal(str(min(scores.values()))),
                        Decimal(str(max(scores.values()))),
                    )
                else:
                    if not isinstance(raw, float):
                        raise ProjectError(f"Non-numeric ranking input: {factor.field}.")
                    number = Decimal(str(raw))
                    minimum, maximum = Decimal(str(factor.minimum)), Decimal(str(factor.maximum))
                normalized = max(
                    Decimal(0), min(Decimal(1), (number - minimum) / (maximum - minimum))
                )
                if factor.lower_is_better:
                    normalized = 1 - normalized
            weight = Decimal(str(factor.weight))
            weighted = weight * normalized
            total += weighted
            weights += weight
            contributions.append(
                ProjectContribution(
                    field=factor.field,
                    field_id=bindings.fields[factor.field].id,
                    raw=raw,
                    normalized=float(normalized),
                    weight=factor.weight,
                    weighted=float(weighted),
                    used_missing_value=missing,
                )
            )
        score = (100 * total / weights).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    return float(score), contributions, None


def _compare_rows(
    left: ProjectRow, right: ProjectRow, sorts: list[ProjectSort], bindings: ProjectBindings
) -> int:
    for sort in sorts:
        a, b = left.values[sort.field], right.values[sort.field]
        if a is None or b is None:
            compared = (a is None) - (b is None)
            if compared:
                return compared
            continue
        field = bindings.fields.get(sort.field)
        if field and field.type == "SINGLE_SELECT":
            order = bindings.option_order[sort.field]
            a_value, b_value = left.item.values[field.id], right.item.values[field.id]
            a, b = (
                float(order.index(a_value.option_ids[0])),
                float(order.index(b_value.option_ids[0])),
            )
        if isinstance(a, list) or isinstance(b, list):
            raise ProjectError("Cannot sort multivalue fields.")
        # Type validation guarantees a homogeneous scalar domain per column.
        compared = (a > b) - (a < b)  # type: ignore[operator]
        if compared:
            return compared if sort.direction == "asc" else -compared
    a_key, b_key = (left.item.identity, left.item.id), (right.item.identity, right.item.id)
    return (a_key > b_key) - (a_key < b_key)


def evaluate(
    config: GitHubProjectsConfig,
    request: ProjectRequest,
    snapshot: ProjectSnapshot,
    bindings: ProjectBindings,
) -> ProjectResult:
    """Evaluate only complete typed data; presentation limits never reduce WIP or ranks."""
    columns = list(request.columns if request.columns is not None else config.columns)
    if request.operation != "items" and request.columns is None:
        columns.extend(alias for alias in ("score", "rank") if alias not in columns)
    if (
        not columns
        or len(set(columns)) != len(columns)
        or any(c not in PROJECT_COLUMNS | bindings.fields.keys() for c in columns)
    ):
        raise ProjectError("Columns must be nonempty, unique known aliases.")
    sorts = _sorts(config, request, bindings)
    filters = _filters(config, request, bindings)
    ranking = request.operation != "items"
    if ranking and not config.ranking.factors:
        raise ProjectError("Ranking requires at least one configured factor.")
    if request.operation == "sync":
        outputs = [alias for alias in (config.outputs.score, config.outputs.rank) if alias]
        if not outputs:
            raise ProjectError("Sync requires at least one configured numeric output field.")
        input_aliases = {factor.field for factor in config.ranking.factors} | {
            a for a, _ in filters if a in bindings.fields
        }
        if config.wip:
            input_aliases.add(config.wip.status)
        output_ids = [bindings.fields[a].id for a in outputs]
        if len(set(output_ids)) != len(output_ids) or set(output_ids) & {
            bindings.fields[a].id for a in input_aliases
        }:
            raise ProjectError(
                "Sync outputs must differ from ranking/filter/WIP inputs by resolved field ID."
            )
    result = ProjectResult(
        operation=request.operation,
        project=snapshot.project,
        columns=columns,
        rows=[],
        visibility=snapshot.visibility,
    )
    eligible_ready: set[str] | None = None
    if request.operation == "pick":
        wip = config.wip
        if wip is None:
            raise ProjectError("Pick requires explicit ready/in-progress status and WIP policy.")
        if wip.pool == "assignee" and not request.assignee:
            raise ProjectError("Assignee WIP pool requires --assignee.")
        current = 0
        for item in snapshot.items:
            if item.archived:
                continue
            if not item.available:
                raise ProjectError("Unavailable item prevents a complete WIP pool.")
            if wip.pool == "assignee":
                if not item.assignees_complete:
                    raise ProjectError("Incomplete assignee data prevents a complete WIP pool.")
                if request.assignee not in item.assignees:
                    continue
            value = _value(item, wip.status, bindings)
            field_value = item.values.get(bindings.fields[wip.status].id)
            if value is None or field_value is None:
                raise ProjectError(f"Missing WIP status on {item.identity}.")
            current += field_value.option_ids[0] in bindings.in_progress
        result.current_wip, result.wip_limit = current, wip.limit
        result.available_slots = max(0, wip.limit - current)
        eligible_ready = set(bindings.ready)
    all_aliases = set(columns) | {s.field for s in sorts}
    for item in snapshot.items:
        if item.archived and (ranking or not request.include_archived):
            continue
        if ranking and not item.available:
            raise ProjectError("Unavailable project content prevents a complete ranking.")
        if not _matches(item, filters, bindings):
            continue
        if request.operation == "pick":
            if item.kind != "Issue" or item.state != "OPEN":
                continue
            assert config.wip is not None and eligible_ready is not None
            status_value = item.values.get(bindings.fields[config.wip.status].id)
            if status_value is None or status_value.option_ids[0] not in eligible_ready:
                continue
        values = {alias: _value(item, alias, bindings) for alias in all_aliases}
        row = ProjectRow(item=item, values=values)
        if ranking:
            score, contributions, reason = _score(item, config, bindings)
            if reason:
                result.excluded.append(
                    ProjectExclusion(item_id=item.id, identity=item.identity, reason=reason)
                )
                continue
            row.score, row.contributions = score, contributions
            row.values["score"] = score
        result.rows.append(row)
    if ranking:
        result.rows.sort(key=lambda row: (-(row.score or 0), row.item.identity, row.item.id))
        for position, row in enumerate(result.rows, 1):
            row.rank = position
            row.values["rank"] = float(position)
    else:
        result.rows.sort(key=cmp_to_key(lambda a, b: _compare_rows(a, b, sorts, bindings)))
    if request.operation == "sync":
        for row in result.rows:
            for alias, new in ((config.outputs.score, row.score), (config.outputs.rank, row.rank)):
                if alias is None or new is None:
                    continue
                old = _value(row.item, alias, bindings)
                if old is not None and not isinstance(old, float):
                    raise ProjectError("Sync output is not numeric.")
                # Exact stored numeric equality is a no-op on a fresh rerun.
                result.operations.append(
                    ProjectSyncOperation(
                        item_id=row.item.id,
                        identity=row.item.identity,
                        field_id=bindings.fields[alias].id,
                        field=alias,
                        old=old,
                        new=float(new),
                        status="noop"
                        if old is not None and Decimal(str(old)) == Decimal(str(new))
                        else "planned",
                    )
                )
        result.reason = "Excluded and filtered-out items retain their existing output values."
    if request.operation == "pick":
        result.rows = result.rows[
            : min(request.count, result.available_slots or 0, request.limit or request.count)
        ]
        if not result.rows:
            result.reason = (
                "WIP pool is full."
                if result.available_slots == 0
                else "No eligible ready open issues."
            )
    elif request.limit is not None:
        result.rows = result.rows[: request.limit]
    result.excluded.sort(key=lambda exclusion: (exclusion.identity, exclusion.item_id))
    return result


def _canonical(snapshot: ProjectSnapshot) -> str:
    data = snapshot.model_dump(mode="json")
    data["items"] = sorted(data["items"], key=lambda item: item["id"])
    data["fields"] = sorted(data["fields"], key=lambda field: field["id"])
    for field in data["fields"]:
        field["options"] = sorted(field["options"], key=lambda option: option["id"])
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _repository(cwd: Path, hostname: str) -> str:
    remote = repo.get_remote_url(cwd) or ""
    parsed = urlparse(remote)
    host, path = parsed.hostname, parsed.path
    if not host:
        match = re.fullmatch(r"(?:[^@]+@)?([^:/]+):(.+)", remote)
        if match:
            host, path = match.groups()
    path = path.removeprefix("/").removesuffix(".git")
    if host != hostname or not re.fullmatch(r"[^/\s]+/[^/\s]+", path):
        raise ProjectError(
            "Pick needs --repository owner/repo when the current repository "
            "cannot be resolved on the target host."
        )
    return path


def execute(
    request: ProjectRequest,
    *,
    cwd: Path | None = None,
    project_config: ProjectConfig | None = None,
    provider: GitHubProjectsProvider | None = None,
) -> ProjectResult:
    """Orchestrate read-only evaluation or explicit, verified sequential numeric writes."""
    cwd = cwd or Path.cwd()
    settings = project_config or load_config(cwd)
    if settings.provider.name != "github":
        raise ProjectError("Project commands currently require provider.name: github.")
    config = settings.github_projects or GitHubProjectsConfig()
    target = request.target or config.target
    if target is None:
        raise ProjectError(
            "Configure github_projects.target or supply complete --owner/--owner-kind/--number."
        )
    from wade.services.check_service import _github_hostname

    inferred_host = _github_hostname(cwd) or "github.com"
    hostname = target.hostname or inferred_host
    if os.environ.get("GH_HOST") and hostname != os.environ["GH_HOST"]:
        raise ProjectError(
            "Explicit target hostname conflicts with GH_HOST; select matching gh authentication."
        )
    if request.operation == "pick" and request.repository is None:
        request = request.model_copy(update={"repository": _repository(cwd, hostname)})
    provider = provider or GitHubProjectsProvider(cwd, hostname)
    multi = any(field.type == ProjectFieldType.MULTI_SELECT for field in config.fields.values())
    schema = provider.schema(target, multi_select=multi)
    bindings = bind(config, schema)
    # Validate syntax and resolved contracts before reading item data.
    _sorts(config, request, bindings)
    filters = _filters(config, request, bindings)
    aliases = (
        set(request.columns or config.columns)
        | {s.field for s in (request.sort if request.sort is not None else config.sort)}
        | {a for a, _ in filters}
    )
    if request.operation != "items":
        aliases |= {factor.field for factor in config.ranking.factors}
    if request.operation == "pick" and config.wip:
        aliases.add(config.wip.status)
        if config.wip.pool == "assignee":
            aliases.add("assignees")
    if request.operation == "sync":
        aliases |= {a for a in (config.outputs.score, config.outputs.rank) if a}
    if any(alias not in PROJECT_COLUMNS | bindings.fields.keys() for alias in aliases):
        raise ProjectError("Unknown column/field alias.")
    required = {bindings.fields[a].id for a in aliases if a in bindings.fields}
    labels = "labels" in aliases or any(
        bindings.fields[a].type == "LABELS" for a in aliases if a in bindings.fields
    )
    assignees = "assignees" in aliases or any(
        bindings.fields[a].type == "ASSIGNEES" for a in aliases if a in bindings.fields
    )
    snapshot = provider.snapshot(schema, required, labels=labels, assignees=assignees)
    result = evaluate(config, request, snapshot, bindings)
    if request.operation != "sync" or not request.apply:
        return result
    if not snapshot.project.viewer_can_update:
        raise ProjectError(
            "Apply requires project write permission; dry-run needs only read access."
        )
    fresh_schema = provider.schema(target, multi_select=multi)
    fresh = provider.snapshot(fresh_schema, required, labels=labels, assignees=assignees)
    if _canonical(fresh) != _canonical(snapshot):
        result.success, result.reason = (
            False,
            "Project changed before apply; zero mutations were sent. Rerun for a fresh preview.",
        )
        for operation in result.operations:
            if operation.status != "noop":
                operation.status = "not_attempted"
        return result
    expected = {item.id: item.model_copy(deep=True) for item in snapshot.items}
    result.applied = True
    stopped = False
    for operation in result.operations:
        if operation.status == "noop":
            continue
        if stopped:
            operation.status = "not_attempted"
            continue
        sent = False
        try:
            current = provider.item(
                schema.project.id, operation.item_id, required, labels=labels, assignees=assignees
            )
            if current != expected[operation.item_id]:
                raise ProjectError(
                    "Item membership/input/output drift detected before mutation; "
                    "rerun to reconcile."
                )
            provider.update_number(
                schema.project.id, operation.item_id, operation.field_id, operation.new
            )
            sent = True
            verified = provider.item(
                schema.project.id, operation.item_id, required, labels=labels, assignees=assignees
            )
            updated = expected[operation.item_id].model_copy(deep=True)
            updated.values[operation.field_id] = ProjectValue(
                field_id=operation.field_id,
                type="ProjectV2ItemFieldNumberValue",
                value=operation.new,
            )
            if verified != updated:
                raise ProjectError(
                    "Numeric write readback differs from expected state; reread before retrying.",
                    unknown_outcome=True,
                )
            expected[operation.item_id] = verified
            operation.status = "confirmed"
        except ProjectError as exc:
            operation.status = "unknown" if exc.unknown_outcome or sent else "failed"
            operation.message = str(exc)
            result.success, result.reason = (
                False,
                (
                    "Sync stopped; confirmed writes remain. Rerun from current GitHub"
                    " truth; no rollback or blind retry was attempted."
                ),
            )
            stopped = True
    return result
