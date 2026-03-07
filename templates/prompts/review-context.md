Let's address the review comments on PR #{pr_number} for Issue #{issue_number}: {issue_title}

Follow @.claude/skills/address-reviews-session/SKILL.md for session rules.

# Review comments to address

Run `wade address-reviews-session fetch {issue_number}` to get all unresolved review comments.
There are {comment_count} unresolved comment(s) across {file_count} file(s).

# Critical rules

1. **First action**: run `wade address-reviews-session check` to verify you're in a worktree.
2. **Fetch comments** with `wade address-reviews-session fetch {issue_number}` — read and address each one.
3. **Verify each finding** against the current code first — only fix it if it's actually a problem.
4. **Resolve threads** with `wade address-reviews-session resolve <thread-id>` after addressing each comment.
5. **Never** push branches or create PRs manually — use `wade address-reviews-session done`.
6. **Before closing**: sync with main via `wade address-reviews-session sync --json`.
