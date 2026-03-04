#!/usr/bin/env bash
# Run the full change checklist: tests + check (lint + types).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$REPO_ROOT/scripts/test.sh"
"$REPO_ROOT/scripts/check.sh"
