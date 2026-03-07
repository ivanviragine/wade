#!/usr/bin/env bash
# Run manual live AI smoke tests (canonical: claude + haiku).
set -euo pipefail

if [[ "${RUN_LIVE_AI_TESTS:-}" != "1" ]]; then
  echo "RUN_LIVE_AI_TESTS must be set to 1."
  exit 1
fi

export WADE_LIVE_AI_TOOL="${WADE_LIVE_AI_TOOL:-claude}"
export WADE_LIVE_AI_MODEL="${WADE_LIVE_AI_MODEL:-claude-haiku-4.5}"

if [[ "${WADE_LIVE_AI_TOOL}" != "claude" ]]; then
  echo "Wave 1 supports only WADE_LIVE_AI_TOOL=claude (got '${WADE_LIVE_AI_TOOL}')."
  exit 1
fi
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ANTHROPIC_API_KEY is required for live AI smoke tests."
  exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI is required but not found in PATH."
  exit 1
fi

exec uv run python -m pytest tests/live/test_wade_live_ai.py -v --tb=short -m "live_ai" "$@"
