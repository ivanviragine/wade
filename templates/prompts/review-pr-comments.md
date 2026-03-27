Let's address the review comments on PR #{pr_number} for Issue #{issue_number}: {issue_title}

Follow @.claude/skills/review-pr-comments-session/SKILL.md for session rules.

# Review comments to address

Run `wade review-pr-comments-session fetch {issue_number}` to get all unresolved review comments.
There are {comment_count} unresolved comment(s) across {file_count} file(s).

# Critical rules

1. **First action**: run `wade review-pr-comments-session check` to verify you're in a worktree.
2. **Fetch comments** with `wade review-pr-comments-session fetch {issue_number}` — read every comment.
3. **Verify each finding** against the current code first — only fix it if it's actually a problem.
4. **Resolve threads** with `wade review-pr-comments-session resolve <thread-id>` after addressing each comment.
5. **Never** push branches or create PRs manually — use `wade review-pr-comments-session done`.
6. **Before closing**: sync with main via `wade review-pr-comments-session sync --json`.
7. **At session end**: always present a workflow recap (which wade commands you ran),
   current state (PR status, threads resolved), and what happens next. Then suggest
   the user exits.
