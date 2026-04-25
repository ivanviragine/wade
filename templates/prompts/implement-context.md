Follow @.claude/skills/implementation-session/SKILL.md for session rules.

Use your tool's native task/todo tracking mechanism to populate a checklist with the workflow steps from the skill before starting work.

# Goal

Let's implement @PLAN.md. Working on Issue #{issue_number}: {issue_title}

If you cannot see the plan contents above, read the file `PLAN.md` in the root of your current directory.

# Critical rules

1. **First action**: run `wade implementation-session check` to verify you're in a worktree.
2. **Never** push branches or create PRs manually — use `wade implementation-session done`.
3. **Before closing**: write `PR-SUMMARY.md` in the worktree root, then sync with main via `wade implementation-session sync --json`.
4. **At session end**: always present a workflow recap (which wade commands you ran),
   current state (PR status, branch, issue), and what happens next. Then suggest
   the user exits.
