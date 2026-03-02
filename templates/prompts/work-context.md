Let's implement @PLAN.md. Working on Issue #{issue_number}: {issue_title}

Follow @.claude/skills/work-session/SKILL.md for session rules.

# Critical rules

1. **First action**: run `wade check` to verify you're in a worktree.
2. **Immediately**: create `/tmp/PR-SUMMARY-{issue_number}.md` as a placeholder.
3. **Never** push branches or create PRs manually — use `wade work done`.
4. **Before closing**: sync with main via `wade work sync --json`.
