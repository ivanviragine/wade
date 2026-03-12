#!/usr/bin/env bash
# Run deterministic E2E contract tests on host (mocked gh, no live credentials).
set -euo pipefail
exec ./scripts/test.sh tests/e2e/ -v --tb=short -m "e2e_docker and contract" "$@"
