#!/usr/bin/env bash
# Run deterministic E2E contract tests inside Docker.
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but not found in PATH."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is required but not available."
  exit 1
fi

PYTEST_ARGS="${*:-}"
exec docker compose -f docker-compose.e2e.yml run --rm -e PYTEST_ARGS="${PYTEST_ARGS}" e2e
