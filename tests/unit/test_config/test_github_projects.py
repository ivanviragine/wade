"""Projects configuration preserves existing local workflows and rejects unsafe intent."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from wade.config.loader import ConfigError, parse_config_file
from wade.models.config import CONFIG_SCHEMA_VERSION, ComplexityModelMapping
from wade.services.check_service import _validate_config_file
from wade.services.init_service.config_io import _patch_config


def project_settings() -> dict:
    return {
        "target": {"owner": "octo-org", "kind": "organization", "number": 7},
        "fields": {
            "impact": {"name": "Impact", "type": "number"},
            "priority": {"id": "F-P", "name": "Priority", "type": "single_select"},
            "status": {"name": "Status", "type": "single_select"},
            "score_output": {"name": "Score", "type": "number"},
            "rank_output": {"name": "Rank", "type": "number"},
        },
        "columns": ["repository", "number", "title", "priority"],
        "sort": [{"field": "impact", "direction": "desc"}],
        "ranking": {
            "factors": [
                {
                    "field": "impact",
                    "weight": 2,
                    "minimum": 0,
                    "maximum": 10,
                    "missing": {"policy": "exclude"},
                },
                {
                    "field": "priority",
                    "weight": 1,
                    "options": {"P0": 100, "P1": 50, "P2": 0},
                    "missing": {"policy": "value", "normalized": 0.25},
                },
            ]
        },
        "wip": {
            "status": "status",
            "ready": ["Todo"],
            "in_progress": ["Doing"],
            "pool": "project",
            "limit": 0,
        },
        "outputs": {"score": "score_output", "rank": "rank_output"},
    }


def config_file(tmp_path: Path, settings: object) -> Path:
    path = tmp_path / ".wade.yml"
    path.write_text(yaml.safe_dump({"version": CONFIG_SCHEMA_VERSION, "github_projects": settings}))
    return path


def test_optional_section_is_loaded_and_preserved_without_network(tmp_path: Path) -> None:
    settings = project_settings()
    path = config_file(tmp_path, settings)
    with patch(
        "subprocess.run", side_effect=AssertionError("config loading must not query GitHub")
    ):
        config = parse_config_file(path)
        assert config.github_projects is not None
        assert config.github_projects.target.number == 7
        assert config.github_projects.wip.limit == 0
        assert config.github_projects.ranking.factors[0].weight == 2
        assert _validate_config_file(path) == []
        _patch_config(path, None, ComplexityModelMapping())
    assert yaml.safe_load(path.read_text())["github_projects"] == settings
    assert parse_config_file(path).github_projects == config.github_projects


def test_absent_section_keeps_existing_defaults(tmp_path: Path) -> None:
    path = tmp_path / ".wade.yml"
    path.write_text("version: " + str(CONFIG_SCHEMA_VERSION) + "\n")
    assert parse_config_file(path).github_projects is None
    _patch_config(path, None, ComplexityModelMapping())
    assert "github_projects" not in yaml.safe_load(path.read_text())


@pytest.mark.parametrize("settings", [None, [], "board", {"unknown": 1}, {"ranking": None}])
def test_invalid_section_fails_loader_and_check_consistently(
    tmp_path: Path, settings: object
) -> None:
    path = config_file(tmp_path, settings)
    with pytest.raises(ConfigError, match=r"github_projects|ranking|unknown"):
        parse_config_file(path)
    assert _validate_config_file(path)


@pytest.mark.parametrize("value", [True, 0, -1, "7", 7.5])
def test_project_number_is_a_strict_positive_integer(tmp_path: Path, value: object) -> None:
    settings = project_settings()
    settings["target"]["number"] = value
    with pytest.raises(ConfigError):
        parse_config_file(config_file(tmp_path, settings))


@pytest.mark.parametrize("value", [True, -1, float("inf"), float("nan"), "1"])
def test_factor_weights_reject_boolean_nonfinite_and_negative_values(
    tmp_path: Path, value: object
) -> None:
    settings = project_settings()
    settings["ranking"]["factors"][0]["weight"] = value
    with pytest.raises(ConfigError):
        parse_config_file(config_file(tmp_path, settings))


@pytest.mark.parametrize(
    "change",
    [
        "all_zero",
        "equal_bounds",
        "missing_policy",
        "output_input",
        "duplicate_output",
        "unknown_nested",
        "bad_sort",
        "unsupported_factor",
    ],
)
def test_invalid_evaluation_contracts_fail_before_provider_calls(
    tmp_path: Path, change: str
) -> None:
    settings = deepcopy(project_settings())
    if change == "all_zero":
        for factor in settings["ranking"]["factors"]:
            factor["weight"] = 0
    elif change == "equal_bounds":
        settings["ranking"]["factors"][0]["maximum"] = 0
    elif change == "missing_policy":
        del settings["ranking"]["factors"][0]["missing"]
    elif change == "output_input":
        settings["outputs"]["score"] = "impact"
    elif change == "duplicate_output":
        settings["outputs"]["rank"] = "score_output"
    elif change == "unknown_nested":
        settings["target"]["project_number"] = 4
    elif change == "bad_sort":
        settings["sort"][0]["direction"] = "sideways"
    else:
        settings["fields"]["impact"]["type"] = "date"
    with pytest.raises(ConfigError):
        parse_config_file(config_file(tmp_path, settings))
