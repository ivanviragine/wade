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

serialize_args() {
  local serialized=()
  local quoted
  for arg in "$@"; do
    printf -v quoted '%q' "$arg"
    serialized+=("$quoted")
  done
  printf '%s ' "${serialized[@]}"
}

PYTEST_ARGS_SHELL=""
if (($# > 0)); then
  PYTEST_ARGS_SHELL="$(serialize_args "$@")"
fi

exec docker compose -f docker-compose.e2e.yml run --rm -e PYTEST_ARGS_SHELL="${PYTEST_ARGS_SHELL}" e2e
