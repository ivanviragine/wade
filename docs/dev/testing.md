# Testing Guide

WADE uses **pytest** exclusively for all test suites.

## Test Organization

```
tests/
├── conftest.py              # Shared fixtures (tmp_git_repo, tmp_wade_project, mock_gh)
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
│   ├── test_hooks/          # Hook tests (plan write guard)
│   └── test_providers/      # Provider tests (GitHub PR, ClickUp, labels)
├── integration/             # Needs git repos, mock gh
│   ├── test_check.py
│   ├── test_init.py
│   ├── test_git.py
│   ├── test_implementation_lifecycle.py
│   └── test_skill_install.py
├── e2e/                     # End-to-end contract tests (mocked gh, deterministic)
│   ├── conftest.py
│   ├── _support.py
│   ├── mock_gh_script.py
│   ├── test_check_contract.py
│   ├── test_plan_contract.py
│   ├── test_review_contract.py # review plan/implementation/pr-comments + session fetch/resolve
│   ├── test_smart_start_contract.py
│   ├── test_task_contract.py
│   ├── test_implement_work_done_contract.py
│   └── test_work_sync_list_contract.py
├── live/                    # Manual live lanes (env-gated)
│   ├── test_wade_live_gh.py
│   ├── test_wade_live_ai.py
│   └── test_wade_live_ai_taskr.py
└── fixtures/                # Static test data files
    ├── live/                # Manual live plan/task fixtures
    └── transcripts/         # Sample AI tool transcripts
```

## Running Tests

```bash
# Host non-live suite (unit + integration + top-level CLI smoke)
./scripts/test.sh

# Full non-live validation = host non-live suite + deterministic E2E contract lane
# Run both when you want CI-equivalent non-live coverage.
# ./scripts/test.sh
# ./scripts/test-e2e-docker.sh

# Unit tests only (fast, no git/subprocess)
./scripts/test.sh tests/unit/

# Integration tests only (needs git, uses mock gh)
./scripts/test.sh tests/integration/

# Specific test file
./scripts/test.sh tests/unit/test_services/test_implementation_done_sync.py

# Run tests matching a pattern
./scripts/test.sh -k "test_check"

# With coverage
./scripts/test.sh --cov=wade --cov-report=term-missing

# Deterministic end-to-end contract lane
./scripts/test-e2e.sh

# Deterministic end-to-end contract lane in Docker (CI-equivalent)
./scripts/test-e2e-docker.sh

# Docker lane is isolated from host .venv (container uses /tmp/wade-e2e-venv)
# so running it does not mutate local development environments.

# Manual live GitHub lane (requires real gh auth + repo)
RUN_LIVE_GH_TESTS=1 WADE_LIVE_REPO=/path/to/repo ./scripts/test-live-gh.sh

# Recommended live GitHub sandbox repo
RUN_LIVE_GH_TESTS=1 \
WADE_LIVE_REPO=/Users/ivanviragine/Documents/workspace/taskr \
./scripts/test-live-gh.sh

# Manual live AI lane (canonical: claude + haiku)
# This validates API-key-backed Claude headless execution, not Claude Code's
# interactive `/login` session auth path.
RUN_LIVE_AI_TESTS=1 ANTHROPIC_API_KEY=... ./scripts/test-live-ai.sh

# Manual live AI workflow lane against taskr (destructive: resets before/after)
RUN_LIVE_AI_TESTS=1 WADE_LIVE_ALLOW_RESET=1 \
WADE_LIVE_REPO=/Users/ivanviragine/Documents/workspace/taskr \
ANTHROPIC_API_KEY=... ./scripts/test-live-ai-taskr.sh
```

Live scripts are strict by design: they fail fast when required env vars,
credentials, or binaries are missing.

## Pytest Markers

- `e2e_docker`: deterministic e2e tests executed in docker/CI lanes
- `contract`: behavior contract tests for CLI/service integration
- `live_gh`: manual live tests requiring gh auth + network
- `live_ai`: manual live tests requiring real AI credentials

## Live Test Environment Contracts

- `RUN_LIVE_GH_TESTS=1` enables live GitHub tests.
- `RUN_LIVE_AI_TESTS=1` enables live AI tests.
- `WADE_LIVE_AI_TOOL=claude` (default)
- `WADE_LIVE_AI_MODEL=claude-haiku-4.5` (default for the provider smoke lane)
- `WADE_LIVE_AI_MODEL=claude-sonnet-4.6` (default for the taskr workflow lane)
- `WADE_LIVE_AI_TIMEOUT=45` (optional timeout in seconds for live AI smoke)
- `WADE_LIVE_AI_WORKFLOW_TIMEOUT=300` (optional timeout in seconds for the taskr workflow lane)
- `ANTHROPIC_API_KEY` is required for the canonical live AI smoke test.
- This lane validates API-key-backed Claude execution and does not verify
  Claude Code's interactive `/login` session auth path.
- `WADE_LIVE_ALLOW_RESET=1` is required for the taskr workflow lane because it
  hard-resets the target repo before and after the run.

Live tests are manual by design. Default CI lanes must not require provider secrets.

Current live GH lane exercises WADE behavior (not raw `gh` checks), including:
- task lifecycle (`task create/read/list/close`)
- PR-backed workflow smoke (`implement --cd` + `review pr-comments` no-comment path)

The recommended manual sandbox repo for both live GH and live AI workflow runs is:
- `/Users/ivanviragine/Documents/workspace/taskr`
- It already contains WADE config plus helper scripts such as
  [reset.sh](/Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh),
  [seed.sh](/Users/ivanviragine/Documents/workspace/taskr/scripts/seed.sh), and
  [smoke.sh](/Users/ivanviragine/Documents/workspace/taskr/scripts/smoke.sh).

Current live AI coverage is split deliberately:
- [tests/live/test_wade_live_ai.py](/Users/ivanviragine/.codex/worktrees/c780/wade/tests/live/test_wade_live_ai.py)
  is the minimal provider smoke (API key + Claude headless + parseable output).
- [tests/live/test_wade_live_ai_taskr.py](/Users/ivanviragine/.codex/worktrees/c780/wade/tests/live/test_wade_live_ai_taskr.py)
  is the taskr workflow lane (real repo, real edits, real tests, real PR lifecycle).
  It uses WADE's real issue/worktree/bootstrap flow plus WADE's implementation
  prompt, then completes via `wade implementation-session done`.
- Live GH and taskr workflow lanes invoke the current checkout via
  `python -m wade`, not an unrelated host-installed `wade` binary.

The taskr workflow lane is host-only and destructive by design:
- it runs outside Docker
- it expects the dedicated `taskr` sandbox repo
- it resets that repo to `reset-target` before and after the run via
  [scripts/reset.sh](/Users/ivanviragine/Documents/workspace/taskr/scripts/reset.sh)
- after each reset, the runner re-initializes WADE locally so the repo stays
  ready for subsequent live runs without requiring a manual `wade init`
- the runner also creates an isolated `CLAUDE_CONFIG_DIR` by default so Claude
  uses the provided API key instead of inheriting an unrelated host login state;
  that isolated config is pre-seeded to skip Claude's first-run onboarding/theme
  prompts
- the actual code-change step runs Claude headlessly against WADE's real
  implementation prompt because Claude's interactive API-key confirmation is not
  automatable in this environment
- unlike the GH live lane, the taskr workflow runner does not require
  `.wade.yml` to exist ahead of time; it recreates the local WADE config itself
- it currently exercises two tiny real tasks in sequence:
  - add a greeting header so `uv run taskr` and `uv run taskr list` both start with `Hi`
  - change that greeting header so `uv run taskr` and `uv run taskr list` both start with `Howdy`

## CI Execution Model

- Full non-live validation requires both `./scripts/test.sh` and `./scripts/test-e2e-docker.sh`.
- Unit + integration + top-level CLI smoke (`tests/test_cli_basics.py`) run directly on the CI host.
- Deterministic E2E contract tests run via `./scripts/test-e2e-docker.sh` and `docker-compose.e2e.yml`.
- Live lanes remain manual and env-gated (`test-live-gh.sh`, `test-live-ai.sh`, `test-live-ai-taskr.sh`).

## Test Fixtures

Key fixtures in `tests/conftest.py`:

| Fixture | Description |
|---------|-------------|
| `tmp_git_repo` | Fresh git repo with initial commit and `main` branch |
| `tmp_wade_project` | Git repo with `.wade.yml` config file (extends `tmp_git_repo`) |
| `monkeypatch_env` | Clears all `WADE_*` env vars to prevent test runner leakage |
| `mock_gh` | Creates a mock `gh` CLI binary that logs invocations; returns path to log file |

## Writing New Tests

New tests belong in the appropriate subdirectory. Use existing test files as reference.

**Basic test skeleton:**
```python
"""Tests for feature X."""

from pathlib import Path
import pytest

def test_feature_does_something(tmp_wade_project: Path) -> None:
    """Feature X should produce expected output."""
    # Arrange
    config_path = tmp_wade_project / ".wade.yml"

    # Act
    result = some_function(config_path)

    # Assert
    assert result.status == "success"
    assert "expected text" in result.output
```

**Testing with mock gh CLI:**
```python
def test_issue_creation(tmp_wade_project: Path, mock_gh: Path) -> None:
    """Creating an issue should invoke gh CLI correctly."""
    result = create_issue(tmp_wade_project, title="Test issue")

    assert result.number == 1
    # Verify gh was called with expected args
    invocations = mock_gh.read_text()
    assert "issue create" in invocations
```

## Test Quality Rules

- Every bug fix must include a regression test that would fail before the fix.
- Use mocks for `gh` CLI and AI tool subprocess calls in unit/integration tests.
- Keep deterministic CI lanes secret-free (mocked `gh` and mocked AI tool process calls).
- For features that interact with external providers, include at least one manual live WADE behavior test in `tests/live/`.
- Gate live GitHub tests with `RUN_LIVE_GH_TESTS=1` and live AI tests with `RUN_LIVE_AI_TESTS=1`.
- Prefer exact assertions over loose substring checks: verify outputs, side effects, and absence of wrong output.
- For `--json` modes, parse stdout as JSON and fail if any non-JSON line appears.
- Pure functions (parsing, formatting, model validation) can and should be tested without mocks.
- In behavior/contract tests, do not compute expected values with the same production helper the code under test uses. Hard-code the contract value or derive it independently so the test can catch helper regressions.

## Recent Cleanup

- Removed low-value live smoke that only validated raw `gh` auth/repo access.
- Removed duplicate installed-binary CLI smoke (`--version`/`--help`) from E2E; kept canonical coverage in `tests/test_cli_basics.py`.
- Replaced it with WADE-behavior live tests (`test_wade_live_gh.py`) and a gated live AI smoke (`test_wade_live_ai.py`).

**When to skip re-running tests after `wade implementation-session sync`:** If the sync merge only brings in changes to documentation or template files (`templates/`, `docs/`, `README.md`, `AGENTS.md`, `CHANGELOG.md`), there is no need to re-run tests. Re-run tests after sync when the merged changes touch `src/`, `scripts/`, or `tests/`.
