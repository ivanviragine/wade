#!/usr/bin/env bash
# Lint and type-check source.
# Flags: --lint   run ruff check + format check only
#        --types  run mypy only
#        (none)   run both
set -euo pipefail

RUN_LINT=true
RUN_TYPES=true

if [[ $# -gt 0 ]]; then
    RUN_LINT=false
    RUN_TYPES=false
    for arg in "$@"; do
        case "$arg" in
            --lint)  RUN_LINT=true ;;
            --types) RUN_TYPES=true ;;
            *) echo "Unknown flag: $arg" >&2; exit 1 ;;
        esac
    done
fi

if $RUN_LINT; then
    echo "--- ruff check ---"
    uv run python -m ruff check src/
    echo "--- ruff format --check ---"
    uv run python -m ruff format --check src/
fi

if $RUN_TYPES; then
    echo "--- mypy --strict ---"
    uv run python -m mypy src/ --strict
fi
