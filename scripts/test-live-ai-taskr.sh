#!/usr/bin/env bash
# Run the manual live AI workflow lane against the real taskr repo.
# This lane is destructive by design: it resets taskr before and after the run.
set -euo pipefail

if [[ "${RUN_LIVE_AI_TESTS:-}" != "1" ]]; then
  echo "RUN_LIVE_AI_TESTS must be set to 1."
  exit 1
fi

LIVE_REPO="${WADE_LIVE_REPO:-${E2E_REPO:-}}"
if [[ -z "${LIVE_REPO}" ]]; then
  echo "Set WADE_LIVE_REPO (or E2E_REPO) to the taskr repo path."
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
if [[ ! -f "${LIVE_REPO}/scripts/reset.sh" ]]; then
  echo "Live repo is missing scripts/reset.sh: ${LIVE_REPO}"
  exit 1
fi
if ! grep -q 'name = "taskr"' "${LIVE_REPO}/pyproject.toml"; then
  echo "Live repo does not appear to be taskr: ${LIVE_REPO}"
  exit 1
fi

if [[ "${WADE_LIVE_ALLOW_RESET:-}" != "1" ]]; then
  echo "WADE_LIVE_ALLOW_RESET must be set to 1 for the destructive taskr workflow lane."
  echo "This runner hard-resets taskr before and after the test via scripts/reset.sh."
  exit 1
fi

export WADE_LIVE_AI_TOOL="${WADE_LIVE_AI_TOOL:-claude}"
export WADE_LIVE_AI_MODEL="${WADE_LIVE_AI_MODEL:-claude-haiku-4.5}"
export WADE_LIVE_AI_WORKFLOW_TIMEOUT="${WADE_LIVE_AI_WORKFLOW_TIMEOUT:-300}"

if [[ "${WADE_LIVE_AI_TOOL}" != "claude" ]]; then
  echo "Taskr live AI workflow supports only WADE_LIVE_AI_TOOL=claude (got '${WADE_LIVE_AI_TOOL}')."
  exit 1
fi
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is required for the taskr live AI workflow."
  exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI is required but not found in PATH."
  exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required but not found in PATH."
  exit 1
fi
if ! command -v wade >/dev/null 2>&1; then
  echo "wade CLI is required but not found in PATH."
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "gh CLI is not authenticated."
  exit 1
fi
if ! [[ "${WADE_LIVE_AI_WORKFLOW_TIMEOUT}" =~ ^[1-9][0-9]*$ ]]; then
  echo "WADE_LIVE_AI_WORKFLOW_TIMEOUT must be a positive integer (got '${WADE_LIVE_AI_WORKFLOW_TIMEOUT}')."
  exit 1
fi

ensure_taskr_wade_ready() {
  local repo="$1"
  local exclude_file="${repo}/.git/info/exclude"

  if ! grep -q '^# wade live taskr$' "${exclude_file}" 2>/dev/null; then
    cat >>"${exclude_file}" <<'EOF'
# wade live taskr
.wade.yml
.wade-managed
.claude/
.wade/
EOF
  fi

  (cd "${repo}" && wade init --ai claude --yes >/dev/null)

  # Keep the repo on the reset-target tree while leaving WADE's local artifacts available.
  (cd "${repo}" && git checkout -- AGENTS.md .gitignore 2>/dev/null || true)
}

echo "Resetting taskr to baseline before live AI workflow..."
(cd "${LIVE_REPO}" && ./scripts/reset.sh --yes)
echo "Re-initializing WADE in taskr after reset..."
ensure_taskr_wade_ready "${LIVE_REPO}"

status=0
env WADE_INCLUDE_LIVE=1 ./scripts/test.sh \
  tests/live/test_wade_live_ai_taskr.py \
  -v \
  --tb=short \
  -m "live_ai and live_gh" \
  "$@" || status=$?

echo "Resetting taskr back to baseline after live AI workflow..."
(cd "${LIVE_REPO}" && ./scripts/reset.sh --yes)
echo "Re-initializing WADE in taskr after cleanup..."
ensure_taskr_wade_ready "${LIVE_REPO}"

exit "${status}"
