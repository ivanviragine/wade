Let's address the review comments on PR #{pr_number} for Issue #{issue_number}: {issue_title}

Follow @.claude/skills/review-session/SKILL.md for session rules.

# Review comments to address

Read @REVIEW-COMMENTS.md for the full list of unresolved review comments.
There are {comment_count} unresolved comment(s) across {file_count} file(s).

# Critical rules

1. **First action**: run `wade check` to verify you're in a worktree.
2. **Address each comment** listed in REVIEW-COMMENTS.md — fix the code, add tests if needed.
3. **Verify each finding** against the current code first — only fix it if it's actually a problem.
4. **Never** push branches or create PRs manually — use `wade work done`.
5. **Before closing**: sync with main via `wade work sync --json`.
