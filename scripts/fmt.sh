#!/usr/bin/env bash
# Auto-format source in-place.
set -euo pipefail
exec uv run python -m ruff format src/
