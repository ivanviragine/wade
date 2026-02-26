#!/usr/bin/env bash
set -euo pipefail
exec uv run python "$(dirname "${BASH_SOURCE[0]}")/probe_models.py" "$@"
