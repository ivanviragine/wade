Let's implement @PLAN.md. Working on Issue #{issue_number}: {issue_title}

Follow @.claude/skills/implementation-session/SKILL.md for session rules.

# Critical rules

1. **First action**: run `wade implementation-session check` to verify you're in a worktree.
2. **Never** push branches or create PRs manually — use `wade implementation-session done`.
3. **Before closing**: write `PR-SUMMARY.md` in the worktree root, then sync with main via `wade implementation-session sync --json`.
