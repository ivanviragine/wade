#!/usr/bin/env bash
# install.sh — Install ghaiwpy (Python ghaiw CLI) using uv into a venv.
#
# Usage:
#   ./install.sh              # Install to default location (~/.local/bin)
#   ./install.sh /usr/local   # Install to custom prefix
#
# Installs as `ghaiwpy` to coexist with the Bash `ghaiw` CLI.
# Requires: uv (https://docs.astral.sh/uv/) — installs it if missing.
# Python 3.11+ is fetched automatically by uv if not available.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${1:-$HOME/.local}"
BIN_DIR="${PREFIX}/bin"
VENV_DIR="${PREFIX}/share/ghaiw/venv"
MIN_PYTHON="3.11"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; }

# ─── Ensure uv ────────────────────────────────────────────────────────────────

if ! command -v uv &>/dev/null; then
    warn "uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

info "uv $(uv --version) found"

# ─── Install ─────────────────────────────────────────────────────────────────

info "Creating virtual environment at ${VENV_DIR}..."
mkdir -p "$(dirname "$VENV_DIR")"

# Let uv find or download a suitable Python (>= 3.11).
# This works even if the system python3 is too old — uv manages its own.
uv venv "$VENV_DIR" --python ">=${MIN_PYTHON}" --clear

PYTHON_VERSION="$("$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
info "Python ${PYTHON_VERSION} (managed by uv)"

info "Installing ghaiwpy..."
uv pip install --python "$VENV_DIR/bin/python" "$SCRIPT_DIR"

# Record source repo path for self-upgrade support
echo "$SCRIPT_DIR" > "$VENV_DIR/ghaiw-source.txt"
info "Recorded source path for self-upgrade"

info "Creating symlink..."
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/ghaiwpy" "$BIN_DIR/ghaiwpy"

# ─── Install git hooks ───────────────────────────────────────────────────────

HOOKS_SCRIPT="${SCRIPT_DIR}/scripts/install-hooks.sh"
if [[ -x "$HOOKS_SCRIPT" ]] && git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
    info "Installing git hooks..."
    bash "$HOOKS_SCRIPT"
fi

# ─── Verify ──────────────────────────────────────────────────────────────────

if "$BIN_DIR/ghaiwpy" --version &>/dev/null; then
    info "ghaiwpy installed successfully!"
    echo ""
    echo "  ghaiwpy $("$BIN_DIR/ghaiwpy" --version 2>/dev/null || echo '(version check failed)')"
    echo "  Binary: ${BIN_DIR}/ghaiwpy"
    echo ""
    if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
        warn "Add ${BIN_DIR} to your PATH:"
        echo "  export PATH=\"${BIN_DIR}:\$PATH\""
    fi
else
    error "Installation failed — ghaiwpy binary not working."
    exit 1
fi
