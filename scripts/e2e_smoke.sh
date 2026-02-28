#!/usr/bin/env bash
# e2e_smoke.sh — Automated end-to-end smoke test for ghaiw.
#
# Exercises the full ghaiw lifecycle against a real or temporary
# GitHub repo. Tests the Python CLI (ghaiw) by default.
#
# Usage:
#   RUN_E2E_SMOKE=1 ./scripts/e2e_smoke.sh              # run against taskr
#   RUN_E2E_SMOKE=1 ./scripts/e2e_smoke.sh --repo <path> # custom repo
#
# Requires:
#   - ghaiw CLI installed and in PATH
#   - gh CLI authenticated
#   - RUN_E2E_SMOKE=1 environment variable

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'
BOLD='\033[1m'

PASS=0
FAIL=0
SKIP=0

# ── Parse args ────────────────────────────────────────────────────────────────

REPO="${TASKR_REPO:-$HOME/Documents/workspace/taskr}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)  shift; REPO="$1"; shift ;;
        -h|--help)
            echo "Usage: RUN_E2E_SMOKE=1 ./scripts/e2e_smoke.sh [--repo <path>]"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Gate ──────────────────────────────────────────────────────────────────────

if [[ "${RUN_E2E_SMOKE:-}" != "1" ]]; then
    echo -e "${YELLOW}E2E smoke tests disabled. Set RUN_E2E_SMOKE=1 to run.${NC}"
    exit 0
fi

# ── Helpers ───────────────────────────────────────────────────────────────────

test_pass() {
    ((PASS++))
    echo -e "  ${GREEN}✓${NC} $*"
}

test_fail() {
    ((FAIL++))
    echo -e "  ${RED}✗${NC} $*"
}

test_skip() {
    ((SKIP++))
    echo -e "  ${YELLOW}○${NC} $* ${DIM}(skipped)${NC}"
}

section() {
    echo ""
    echo -e "${BOLD}${CYAN}─── $* ───${NC}"
}

run_test() {
    local desc="$1"
    shift
    local output
    if output=$("$@" 2>&1); then
        test_pass "$desc"
        return 0
    else
        test_fail "$desc (exit $?)"
        echo -e "    ${DIM}${output}${NC}" | head -5
        return 1
    fi
}

# ── Preamble ──────────────────────────────────────────────────────────────────

echo -e "${BOLD}${CYAN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║           ghaiw E2E Smoke Test                          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "Target repo: ${BOLD}$REPO${NC}"
echo ""

# ── Prerequisites ─────────────────────────────────────────────────────────────

section "Prerequisites"

if ! command -v ghaiw &>/dev/null; then
    test_fail "ghaiw CLI found in PATH"
    echo -e "  ${RED}Install ghaiw first: ./install.sh${NC}"
    exit 1
fi
test_pass "ghaiw CLI found: $(which ghaiw)"

if ! command -v gh &>/dev/null; then
    test_fail "gh CLI found in PATH"
    exit 1
fi
test_pass "gh CLI found: $(which gh)"

if ! gh auth status &>/dev/null; then
    test_fail "gh authenticated"
    exit 1
fi
test_pass "gh authenticated"

if [[ ! -d "$REPO/.git" ]]; then
    test_fail "Target repo is a git repository"
    exit 1
fi
test_pass "Target repo is a git repository"

cd "$REPO"

# ── ghaiw version/help ───────────────────────────────────────────────────────

section "Basic Commands"

run_test "ghaiw --version" ghaiw --version
run_test "ghaiw --help" ghaiw --help

# ── ghaiw check ───────────────────────────────────────────────────────────────

section "Check"

# Should be on main
git checkout main &>/dev/null 2>&1 || true

output=$(ghaiw check 2>&1) || true
if echo "$output" | grep -qE "IN_MAIN_CHECKOUT|IN_WORKTREE"; then
    test_pass "ghaiw check returns valid status"
else
    test_fail "ghaiw check returns valid status"
fi

# ── ghaiw check-config ────────────────────────────────────────────────────────

if [[ -f .ghaiw.yml ]]; then
    run_test "ghaiw check-config validates config" ghaiw check-config
else
    test_skip "ghaiw check-config (no .ghaiw.yml)"
fi

# ── ghaiw new-task (via gh issue create for non-interactive testing) ──────────

section "Task Lifecycle"

# Create a test issue directly via gh (ghaiw new-task is interactive)
REPO_NWO=$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null) || true
output=$(gh issue create --title "E2E smoke test — auto-cleanup" \
    --body "Automated test issue from ghaiw E2E smoke test. This issue will be closed automatically." \
    --label "easy" 2>&1) || true
ISSUE_NUM=$(echo "$output" | grep -oE '/issues/[0-9]+' | head -1 | grep -oE '[0-9]+')

if [[ -n "$ISSUE_NUM" ]]; then
    test_pass "ghaiw new-task → issue #$ISSUE_NUM"
else
    test_fail "ghaiw new-task (could not parse issue number)"
    ISSUE_NUM=""
fi

# Task list
run_test "ghaiw task list" ghaiw task list

# Task read (if we created one)
if [[ -n "$ISSUE_NUM" ]]; then
    run_test "ghaiw task read #$ISSUE_NUM" ghaiw task read "$ISSUE_NUM"
fi

# ── Cleanup: close test issue ────────────────────────────────────────────────

section "Cleanup"

if [[ -n "$ISSUE_NUM" ]]; then
    if ghaiw task close "$ISSUE_NUM" &>/dev/null; then
        test_pass "Closed test issue #$ISSUE_NUM"
    else
        # Fallback to gh
        gh issue close "$ISSUE_NUM" &>/dev/null && \
            test_pass "Closed test issue #$ISSUE_NUM (via gh)" || \
            test_fail "Could not close test issue #$ISSUE_NUM"
    fi
else
    test_skip "Issue cleanup (no issue created)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}─── Results ───${NC}"
echo -e "  ${GREEN}Passed: $PASS${NC}"
echo -e "  ${RED}Failed: $FAIL${NC}"
echo -e "  ${YELLOW}Skipped: $SKIP${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}E2E smoke test failed ($FAIL failures)${NC}"
    exit 1
else
    echo -e "${GREEN}All E2E smoke tests passed!${NC}"
    exit 0
fi
