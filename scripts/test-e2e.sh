#!/usr/bin/env bash
# Run deterministic E2E contract tests (mocked gh, no live credentials).
set -euo pipefail
exec uv run python -m pytest tests/e2e/ -v --tb=short -m "e2e_docker and contract" "$@"
