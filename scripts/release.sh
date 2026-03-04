#!/usr/bin/env bash
# release.sh — Publish the current version to PyPI and create a GitHub Release.
#
# Usage:
#   ./scripts/release.sh              # publish current version
#   ./scripts/release.sh --dry-run    # preview without publishing

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# Read current version
VERSION=$(python3 -c "import re; print(re.search(r'__version__\s*=\s*\"(.+?)\"', open('src/wade/__init__.py').read()).group(1))")
TAG="v${VERSION}"

echo "=== Release wade-cli ${TAG} ==="
echo ""

# Check working tree is clean
if [[ -n "$(git status --porcelain)" ]]; then
    echo "Error: working tree is not clean. Commit or stash changes first."
    exit 1
fi

# Check tag exists (auto_version.py should have created it on push)
if ! git tag -l "$TAG" | grep -q "$TAG"; then
    echo "Error: tag ${TAG} not found. Did you push first? (pre-push hook creates the tag)"
    exit 1
fi

# Check not already published on PyPI
if pip index versions wade-cli 2>/dev/null | grep -q "$VERSION"; then
    echo "Error: wade-cli ${VERSION} is already on PyPI."
    exit 1
fi

echo "Version:  ${VERSION}"
echo "Tag:      ${TAG}"
echo "Package:  wade-cli"
echo ""

if $DRY_RUN; then
    echo "(dry run — no changes made)"
    exit 0
fi

# Build
rm -rf dist
uv build
echo ""

# Resolve PyPI token
PYPI_TOKEN="${UV_PUBLISH_TOKEN:-}"

if [[ -z "$PYPI_TOKEN" && -f "$HOME/.pypirc" ]]; then
    PYPI_TOKEN=$(python3 -c "
import configparser, pathlib
c = configparser.ConfigParser()
c.read(pathlib.Path.home() / '.pypirc')
print(c.get('pypi', 'password', fallback=''))
" 2>/dev/null || true)
    [[ -n "$PYPI_TOKEN" ]] && echo "Using token from ~/.pypirc"
fi

if [[ -z "$PYPI_TOKEN" ]]; then
    echo "No token found in UV_PUBLISH_TOKEN or ~/.pypirc"
    printf "Enter PyPI token: "
    read -r PYPI_TOKEN
    if [[ -z "$PYPI_TOKEN" ]]; then
        echo "Error: no token provided."
        exit 1
    fi
fi

# Publish to PyPI
uv publish --token "$PYPI_TOKEN"
echo ""

# Create GitHub Release
if gh release view "$TAG" &>/dev/null; then
    echo "GitHub Release ${TAG} already exists — skipping."
else
    gh release create "$TAG" --title "$TAG" --generate-notes
    echo "GitHub Release ${TAG} created."
fi

echo ""
echo "=== Released wade-cli ${TAG} ==="
