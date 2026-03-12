#!/usr/bin/env bash
# install.sh — Install WADE from PyPI using uv tool.
#
# Usage:
#   curl -LsSf https://raw.githubusercontent.com/ivanviragine/wade/main/install.sh | sh
#   ./install.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; }

# ─── Ensure uv ────────────────────────────────────────────────────────────────

if ! command -v uv &>/dev/null; then
    warn "uv not found — installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

info "uv $(uv --version) found"

# ─── Install ──────────────────────────────────────────────────────────────────

info "Installing WADE..."
uv tool install wade-cli

# ─── Verify ───────────────────────────────────────────────────────────────────

if command -v wade &>/dev/null; then
    info "WADE installed successfully!"
    echo ""
    echo "  $(wade --version)"
    echo ""
    echo "  To get started:  wade init"
    echo "  To upgrade:      wade update"
    echo ""
else
    warn "wade binary not found in PATH — you may need to add uv's bin directory:"
    echo ""
    echo '  export PATH="$HOME/.local/bin:$PATH"'
    echo ""
    echo "  Then restart your shell and run: wade --version"
fi
