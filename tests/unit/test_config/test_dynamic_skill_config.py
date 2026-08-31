"""Config contracts for dynamic session and delegation skill bindings."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.config.loader import ConfigError, parse_config_file
from wade.models.skill import SkillSource
from wade.services.check_service import ConfigExitCode, validate_config


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".wade.yml"
    path.write_text(content, encoding="utf-8")
    return path


def test_new_sections_are_optional_and_default_empty(tmp_path: Path) -> None:
    config = parse_config_file(_write(tmp_path, "version: 2\n"))
    assert config.skills.project.discover is True
    assert config.skills.project.include == ("*",)
    assert config.sessions.implementation.skills.work is None
    assert config.delegations.code_review.skills.work is None


def test_parses_ordered_session_and_delegation_bindings(tmp_path: Path) -> None:
    config = parse_config_file(
        _write(
            tmp_path,
            """version: 2
skills:
  project:
    discover: true
    include: ["*"]
    exclude: [legacy-*]
sessions:
  implementation:
    skills:
      work:
        - builtin:implementation
        - project:django
      review:
        - path:.agents/skills/security
delegations:
  code_review:
    skills:
      work: [builtin:code-review]
""",
        )
    )
    work = config.sessions.implementation.skills.work
    assert work is not None
    assert [ref.canonical for ref in work] == [
        "builtin:implementation",
        "project:django",
    ]
    review = config.sessions.implementation.skills.review
    assert review is not None and review[0].source is SkillSource.PATH
    assert config.delegations.code_review.skills.work is not None


@pytest.mark.parametrize(
    "body",
    [
        "sessions:\n  deps:\n    skills:\n      work: [builtin:dependency-analysis]\n",
        "sessions:\n  implementation:\n    skills:\n      unsupported: [builtin:implementation]\n",
        "delegations:\n  code_review:\n    skills:\n      review: [builtin:code-review]\n",
        "sessions:\n  implementation:\n    skills:\n      work: []\n",
        "sessions:\n  plan:\n    skills:\n      work: [planning]\n",
    ],
)
def test_invalid_slots_and_refs_fail_load_and_check_config(tmp_path: Path, body: str) -> None:
    path = _write(tmp_path, "version: 2\n" + body)
    with pytest.raises(ConfigError):
        parse_config_file(path)
    result = validate_config(tmp_path)
    assert result.exit_code is ConfigExitCode.INVALID
    assert result.errors


def test_check_config_rejects_missing_active_project_ref(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """version: 2
sessions:
  implementation:
    skills:
      work: [project:not-installed]
""",
    )
    result = validate_config(tmp_path)
    assert result.exit_code is ConfigExitCode.INVALID
    assert any("project:not-installed" in error for error in result.errors)


def test_check_config_warns_when_session_reviewer_shadows_delegation(tmp_path: Path) -> None:
    _write(
        tmp_path,
        """version: 2
sessions:
  implementation:
    skills:
      review: [builtin:code-review]
delegations:
  code_review:
    skills:
      work: [builtin:plan-review]
""",
    )
    result = validate_config(tmp_path)
    assert result.exit_code is ConfigExitCode.VALID
    assert any("shadows differing" in warning for warning in result.warnings)
    assert "warning:" in result.format_output()
