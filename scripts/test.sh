#!/usr/bin/env bash
# Run the test suite. Extra args are forwarded to pytest.
# Usage:
#   ./scripts/test.sh              # all tests (excludes live)
#   ./scripts/test.sh tests/unit/  # unit tests only
set -euo pipefail
exec uv run python -m pytest "${@:-tests/}" --ignore=tests/live
