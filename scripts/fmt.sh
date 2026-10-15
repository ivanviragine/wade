#!/usr/bin/env bash
# Auto-format source in-place.
set -euo pipefail
if [[ $# -eq 0 ]]; then
    set -- src/
fi
exec uv run python -m ruff format "$@"
