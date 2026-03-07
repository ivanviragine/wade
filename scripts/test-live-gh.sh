#!/usr/bin/env bash
# Run manual live GH tests for WADE behavior.
set -euo pipefail

if [[ "${RUN_LIVE_GH_TESTS:-}" != "1" ]]; then
  echo "RUN_LIVE_GH_TESTS must be set to 1."
  exit 1
fi

LIVE_REPO="${WADE_LIVE_REPO:-${E2E_REPO:-}}"
if [[ -z "${LIVE_REPO}" ]]; then
  echo "Set WADE_LIVE_REPO (or E2E_REPO) to a live repo path."
  exit 1
fi
if ! git -C "${LIVE_REPO}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Live repo is not a git worktree: ${LIVE_REPO}"
  exit 1
fi
if [[ ! -f "${LIVE_REPO}/.wade.yml" ]]; then
  echo "Live repo is missing .wade.yml: ${LIVE_REPO}"
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required but not found in PATH."
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "gh CLI is not authenticated."
  exit 1
fi

exec uv run python -m pytest tests/live/test_wade_live_gh.py -v --tb=short -m "live_gh" "$@"
