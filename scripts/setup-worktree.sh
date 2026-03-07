#!/usr/bin/env bash
# Set up a new worktree with dev dependencies and git hooks.
#
# This script is called automatically by the post_worktree_create hook
# when a new worktree is created via `wade implement`.
#
# It installs:
# - Dev dependencies via `uv pip install -e ".[dev]"`
# - Git hooks via `scripts/install-hooks.sh`

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Setting up worktree in ${ROOT_DIR}..."
echo ""

# Create/ensure virtual environment and install dev dependencies
echo "Installing dev dependencies..."
uv sync --all-extras
echo ""

# Install git hooks
echo "Installing git hooks..."
"${SCRIPT_DIR}/install-hooks.sh"
echo ""

echo "✓ Worktree setup complete!"
