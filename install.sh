#!/usr/bin/env bash
# install.sh — Install ghaiwpy (Python ghaiw CLI) using uv into a venv.
#
# Usage:
#   ./install.sh              # Install to default location (~/.local/bin)
#   ./install.sh /usr/local   # Install to custom prefix
#
# Installs as `ghaiwpy` to coexist with the Bash `ghaiw` CLI.
# Requires: Python 3.11+, uv (https://docs.astral.sh/uv/)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${1:-$HOME/.local}"
BIN_DIR="${PREFIX}/bin"
VENV_DIR="${PREFIX}/share/ghaiw/venv"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; }

# ─── Checks ──────────────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    error "Python 3 is required but not found."
    exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
MAJOR="${PYTHON_VERSION%%.*}"
MINOR="${PYTHON_VERSION##*.}"

if [[ "$MAJOR" -lt 3 ]] || { [[ "$MAJOR" -eq 3 ]] && [[ "$MINOR" -lt 11 ]]; }; then
    error "Python 3.11+ is required (found ${PYTHON_VERSION})."
    exit 1
fi

if ! command -v uv &>/dev/null; then
    warn "uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

info "Python ${PYTHON_VERSION} found"
info "uv $(uv --version) found"

# ─── Install ─────────────────────────────────────────────────────────────────

info "Creating virtual environment at ${VENV_DIR}..."
mkdir -p "$(dirname "$VENV_DIR")"
uv venv "$VENV_DIR" --python "python${PYTHON_VERSION}"

info "Installing ghaiwpy..."
uv pip install --python "$VENV_DIR/bin/python" "$SCRIPT_DIR"

info "Creating symlink..."
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/ghaiwpy" "$BIN_DIR/ghaiwpy"

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
