Let's implement @PLAN.md. Working on Issue #{issue_number}: {issue_title}

If you cannot see the plan contents above, read the file `PLAN.md` in the root of your current directory.

Follow @.claude/skills/implementation-session/SKILL.md for session rules.

If KNOWLEDGE.md exists, read it for project context.

# Critical rules

1. **First action**: run `wade implementation-session check` to verify you're in a worktree.
2. **Never** push branches or create PRs manually — use `wade implementation-session done`.
3. **Before closing**: write `PR-SUMMARY.md` in the worktree root, then sync with main via `wade implementation-session sync --json`.
