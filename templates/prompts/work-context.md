Let's implement @PLAN.md. Working on Issue #{issue_number}: {issue_title}

Follow @.claude/skills/work-session/SKILL.md for session rules.

# Critical rules

1. **First action**: run `wade check` to verify you're in a worktree.
2. **Never** push branches or create PRs manually — use `wade work done`.
3. **Before closing**: write `PR-SUMMARY.md` in the worktree root, then sync with main via `wade work sync --json`.
