"""Projects config rejects unsafe coercions and incompatible operation contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wade.models.project import GitHubProjectsConfig, ProjectFactor, ProjectFieldRef, ProjectRequest


@pytest.mark.parametrize(
    "key", ["target", "wip", "ranking", "outputs", "fields", "sort", "columns"]
)
def test_present_null_sections_are_not_silent_defaults(key: str) -> None:
    with pytest.raises(ValidationError):
        GitHubProjectsConfig.model_validate({key: None})


@pytest.mark.parametrize(
    "values",
    [
        {"id": "F", "type": "NUMBER", "options": {"one": {"id": "O"}}},
        {"id": "F", "type": "MULTI_SELECT", "option_order": ["O"]},
        {"id": "F", "type": "unsupported"},
        {"type": "NUMBER"},
    ],
)
def test_invalid_field_mappings_fail_before_network(values: object) -> None:
    with pytest.raises(ValidationError):
        ProjectFieldRef.model_validate(values)


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_ranking_boolean_policy_is_strict(value: object) -> None:
    with pytest.raises(ValidationError):
        ProjectFactor.model_validate(
            {
                "field": "n",
                "minimum": 0,
                "maximum": 1,
                "lower_is_better": value,
                "missing": {"policy": "exclude"},
            }
        )


def test_apply_is_never_configurable_and_requires_sync_intent() -> None:
    with pytest.raises(ValidationError):
        GitHubProjectsConfig.model_validate({"apply": True})
    for operation in ("items", "rank", "pick"):
        with pytest.raises(ValidationError):
            ProjectRequest.model_validate({"operation": operation, "apply": True})
