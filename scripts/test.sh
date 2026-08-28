#!/usr/bin/env bash
# Run the test suite. Extra args are forwarded to pytest.
# Usage:
#   ./scripts/test.sh              # all tests (excludes live)
#   ./scripts/test.sh tests/unit/  # unit tests only
set -euo pipefail

# Keep Rich/Typer output deterministic even when the caller's terminal advertises
# color support. ANSI sequences otherwise make text assertions environment-dependent.
export NO_COLOR=1

if [[ "${WADE_INCLUDE_LIVE:-}" == "1" ]]; then
  exec uv run python -m pytest "${@:-tests/}"
fi

exec uv run python -m pytest "${@:-tests/}" --ignore=tests/live
