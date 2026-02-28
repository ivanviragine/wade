#!/usr/bin/env bash
# setup-release.sh — One-time release infrastructure setup.
#
# Run this once before your first release to wire up:
#   - GitHub 'pypi' environment (required for trusted publishing)
#   - First PyPI upload (to claim the package name)
#   - Guidance for PyPI trusted publishing (one-time web UI step)
#
# Prerequisites: gh (authenticated), uv, git, curl

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

info()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn()   { echo -e "${YELLOW}[!]${NC} $*"; }
error()  { echo -e "${RED}[✗]${NC} $*" >&2; }
step()   { echo -e "\n${BOLD}── $* ──${NC}"; }
ask()    { read -rp "    $1 [y/N] " _ans; [[ "${_ans,,}" == "y" ]]; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ─── 1. Prerequisites ─────────────────────────────────────────────────────────

step "Checking prerequisites"
for cmd in gh uv git curl; do
    if command -v "$cmd" &>/dev/null; then
        info "$cmd found"
    else
        error "$cmd is required but not installed."
        [[ "$cmd" == "gh" ]] && echo "  Install: https://cli.github.com/"
        [[ "$cmd" == "uv" ]] && echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
done

if ! gh auth status &>/dev/null; then
    error "gh CLI is not authenticated. Run: gh auth login"
    exit 1
fi
info "gh authenticated"

# ─── 2. Detect repo + package info ────────────────────────────────────────────

step "Detecting project info"

OWNER=$(gh repo view --json owner -q '.owner.login' 2>/dev/null || echo "")
REPO=$(gh repo view --json name -q '.name' 2>/dev/null || echo "")
if [[ -z "$OWNER" || -z "$REPO" ]]; then
    error "Could not detect GitHub repo. Run 'gh repo view' to confirm it's set up."
    exit 1
fi

PACKAGE_NAME=$(python3 -c "
import re, pathlib
content = pathlib.Path('pyproject.toml').read_text()
m = re.search(r'^name\s*=\s*\"([^\"]+)\"', content, re.MULTILINE)
print(m.group(1) if m else '')
")
if [[ -z "$PACKAGE_NAME" ]]; then
    error "Could not read package name from pyproject.toml"
    exit 1
fi

info "GitHub repo:  $OWNER/$REPO"
info "PyPI package: $PACKAGE_NAME"

# ─── 3. Repo visibility ───────────────────────────────────────────────────────

step "Checking repo visibility"
VISIBILITY=$(gh repo view --json visibility -q '.visibility')
if [[ "$VISIBILITY" == "PUBLIC" ]]; then
    info "Repo is public"
else
    warn "Repo is currently ${VISIBILITY} — the install.sh one-liner requires a public repo"
    if ask "Make the repo public now?"; then
        gh repo edit --visibility public
        info "Repo is now public"
    else
        warn "Skipping — remember to make it public before sharing the install one-liner"
    fi
fi

# ─── 4. GitHub 'pypi' environment ────────────────────────────────────────────

step "Creating GitHub 'pypi' environment"
HTTP_STATUS=$(gh api "repos/$OWNER/$REPO/environments/pypi" \
    -X PUT \
    -f 'wait_timer=0' \
    --silent \
    --include \
    2>/dev/null | head -1 | awk '{print $2}')

if [[ "$HTTP_STATUS" == "200" || "$HTTP_STATUS" == "201" || "$HTTP_STATUS" == "" ]]; then
    info "GitHub 'pypi' environment ready"
else
    warn "Unexpected response ($HTTP_STATUS) — check manually at:"
    warn "https://github.com/$OWNER/$REPO/settings/environments"
fi

# ─── 5. Build wheel ──────────────────────────────────────────────────────────

step "Building wheel"
rm -rf dist/
uv build
WHEEL=$(ls dist/*.whl 2>/dev/null | head -1)
if [[ -z "$WHEEL" ]]; then
    error "No wheel found after build — check 'uv build' output above"
    exit 1
fi
info "Built: $(basename "$WHEEL")"

# ─── 6. First PyPI upload ─────────────────────────────────────────────────────

step "Checking PyPI"
PYPI_VERSION=$(curl -sf "https://pypi.org/pypi/$PACKAGE_NAME/json" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['info']['version'])" \
    2>/dev/null || echo "")

if [[ -n "$PYPI_VERSION" ]]; then
    info "'$PACKAGE_NAME' already on PyPI (v$PYPI_VERSION) — first upload not needed"
else
    warn "'$PACKAGE_NAME' is not yet on PyPI"
    echo ""
    echo "  A one-time manual upload is needed to claim the package name."
    echo "  After this, CI will publish automatically via OIDC (no token stored)."
    echo ""
    echo "  Steps:"
    echo "    1. Create a PyPI account: https://pypi.org/account/register/"
    echo "    2. Create an API token:   https://pypi.org/manage/account/token/"
    echo "    3. Upload now:"
    echo "       TWINE_PASSWORD=<your-token> uvx twine upload --username __token__ dist/*"
    echo ""
    echo "  Run the upload command above, then press Enter to continue."
    read -rp "    Press Enter when done (or Ctrl-C to exit and run this script again later)..."

    PYPI_VERSION=$(curl -sf "https://pypi.org/pypi/$PACKAGE_NAME/json" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['info']['version'])" \
        2>/dev/null || echo "")

    if [[ -n "$PYPI_VERSION" ]]; then
        info "Upload confirmed — '$PACKAGE_NAME' v$PYPI_VERSION is live on PyPI"
    else
        warn "Could not verify upload — PyPI may take a few minutes to index it"
        warn "Continue only if you're sure the upload succeeded"
    fi
fi

# ─── 7. PyPI trusted publishing instructions ──────────────────────────────────

step "PyPI trusted publishing setup"
echo ""
echo "  Configure trusted publishing so CI can publish without storing secrets:"
echo ""
echo "  ${BOLD}URL:${NC} https://pypi.org/manage/project/$PACKAGE_NAME/settings/publishing/"
echo ""
echo "  Click 'Add a new publisher' and fill in:"
echo ""
printf "  %-20s %s\n" "Publisher:"     "GitHub Actions"
printf "  %-20s %s\n" "Owner:"         "$OWNER"
printf "  %-20s %s\n" "Repository:"    "$REPO"
printf "  %-20s %s\n" "Workflow name:" "publish.yml"
printf "  %-20s %s\n" "Environment:"   "pypi"
echo ""
echo "  (Copy the values above into the PyPI form and save)"
echo ""
if ask "Mark trusted publishing as configured?"; then
    info "Trusted publishing marked as done"
else
    warn "Remember to complete this step before publishing your first release via CI"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

echo ""
info "Setup complete!"
echo ""
echo "  ${BOLD}Release workflow:${NC}"
echo "    python scripts/auto_version.py patch --push"
echo "    → Go to GitHub Releases, review the draft, click Publish"
echo "    → CI publishes to PyPI automatically"
echo ""
