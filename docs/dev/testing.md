# Testing Guide

ghaiw uses **pytest** exclusively for all test suites.

## Test Organization

```
tests/
├── conftest.py              # Shared fixtures (tmp_git_repo, tmp_ghaiw_project, mock_gh)
├── test_cli_basics.py       # Basic CLI command tests (help, version)
├── unit/                    # Pure logic, no subprocess, no git
│   ├── test_models/         # Pydantic model tests
│   ├── test_services/       # Service logic tests (mocked providers/git)
│   ├── test_config/         # Config parsing tests
│   ├── test_transcript/     # Transcript parsing tests
│   ├── test_utils/          # Utility function tests
│   ├── test_cli/            # CLI command tests (shell-init, AI flags)
│   ├── test_ai_tools/       # AI tool adapter and registry tests
│   ├── test_db/             # Database repository tests
│   ├── test_git/            # Git operation tests (branch, sync, worktree)
│   └── test_providers/      # Provider tests (GitHub PR, labels)
├── integration/             # Needs git repos, mock gh
│   ├── test_check.py
│   ├── test_init.py
│   ├── test_git.py
│   ├── test_work_lifecycle.py
│   └── test_skill_install.py
├── e2e/                     # End-to-end smoke tests
│   ├── test_live_workflow.py
│   └── test_workflow_chain.py
├── live/                    # Needs gh auth + network (gated by RUN_LIVE_GH_TESTS=1)
│   └── test_gh_integration.py
└── fixtures/                # Static test data files
    ├── config_files/
    ├── plan_files/
    └── transcripts/
```

## Running Tests

```bash
# All tests (excluding live)
./scripts/test.sh

# Unit tests only (fast, no git/subprocess)
./scripts/test.sh tests/unit/

# Integration tests only (needs git, uses mock gh)
./scripts/test.sh tests/integration/

# Specific test file
./scripts/test.sh tests/unit/test_services/test_work_done_sync.py

# Run tests matching a pattern
./scripts/test.sh -k "test_check"

# With coverage
./scripts/test.sh --cov=ghaiw --cov-report=term-missing

# Live GitHub tests (requires real gh auth)
RUN_LIVE_GH_TESTS=1 uv run python -m pytest tests/live/ -v
```

## Test Fixtures

Key fixtures in `tests/conftest.py`:

| Fixture | Description |
|---------|-------------|
| `tmp_git_repo` | Fresh git repo with initial commit and `main` branch |
| `tmp_ghaiw_project` | Git repo with `.ghaiw.yml` config file (extends `tmp_git_repo`) |
| `monkeypatch_env` | Clears all `GHAIW_*` env vars to prevent test runner leakage |
| `mock_gh` | Creates a mock `gh` CLI binary that logs invocations; returns path to log file |

## Writing New Tests

New tests belong in the appropriate subdirectory. Use existing test files as reference.

**Basic test skeleton:**
```python
"""Tests for feature X."""

from pathlib import Path
import pytest

def test_feature_does_something(tmp_ghaiw_project: Path) -> None:
    """Feature X should produce expected output."""
    # Arrange
    config_path = tmp_ghaiw_project / ".ghaiw.yml"

    # Act
    result = some_function(config_path)

    # Assert
    assert result.status == "success"
    assert "expected text" in result.output
```

**Testing with mock gh CLI:**
```python
def test_issue_creation(tmp_ghaiw_project: Path, mock_gh: Path) -> None:
    """Creating an issue should invoke gh CLI correctly."""
    result = create_issue(tmp_ghaiw_project, title="Test issue")

    assert result.number == 1
    # Verify gh was called with expected args
    invocations = mock_gh.read_text()
    assert "issue create" in invocations
```

## Test Quality Rules

- Every bug fix must include a regression test that would fail before the fix.
- Use mocks for `gh` CLI and AI tool subprocess calls in unit/integration tests.
- For features that interact with GitHub, include at least one real `gh` integration test in `tests/live/`; mocks alone are not enough.
- Gate live GitHub tests with `RUN_LIVE_GH_TESTS=1` and skip explicitly when prerequisites are missing.
- Prefer exact assertions over loose substring checks: verify outputs, side effects, and absence of wrong output.
- For `--json` modes, parse stdout as JSON and fail if any non-JSON line appears.
- Pure functions (parsing, formatting, model validation) can and should be tested without mocks.

**When to skip re-running tests after `ghaiw work sync`:** If the sync merge only brings in changes to documentation or template files (`templates/`, `docs/`, `README.md`, `AGENTS.md`, `CHANGELOG.md`), there is no need to re-run tests. Re-run tests after sync when the merged changes touch `src/`, `scripts/`, or `tests/`.
