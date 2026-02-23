#!/usr/bin/env bash
# Install ghaiw-py git hooks into .git/hooks/ for this repository.
#
# Usage: ./scripts/install-hooks.sh [--force]
#
# The hooks are sourced from scripts/hooks/ and installed into .git/hooks/.
# Existing hooks are left in place unless --force is given.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
HOOKS_SRC="${SCRIPT_DIR}/hooks"
HOOKS_DST="${ROOT_DIR}/.git/hooks"

FORCE=false
[[ "${1:-}" == "--force" ]] && FORCE=true

if [[ ! -d "$HOOKS_DST" ]]; then
  # In a git worktree, .git is a file pointing to the worktree gitdir.
  # Resolve the common git dir to find the shared hooks directory.
  common_git_dir="$(git rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$common_git_dir" && -d "${common_git_dir}/hooks" ]]; then
    HOOKS_DST="${common_git_dir}/hooks"
  else
    echo "✗ .git/hooks/ not found — are you in a git repository?"
    exit 1
  fi
fi

install_hook() {
  local name="$1"
  local src="${HOOKS_SRC}/${name}"
  local dst="${HOOKS_DST}/${name}"

  if [[ ! -f "$src" ]]; then
    echo "✗ Source hook not found: ${src}"
    return 1
  fi

  if [[ -f "$dst" ]] && ! $FORCE; then
    echo "  Skipped (already exists): ${dst}  (use --force to overwrite)"
    return 0
  fi

  cp "$src" "$dst"
  chmod +x "$dst"
  echo "✓ Installed: ${dst}"
}

install_hook "pre-push"

echo ""
echo "Hooks installed. Run 'git push' to verify."
