Follow @.claude/skills/review-pr-comments-session/SKILL.md for session rules —
build a todo list from its workflow steps before starting work.

# Goal

Address the review comments on PR #{pr_number} for Issue #{issue_number}:
{issue_title}. There are {comment_count} unresolved comment(s) across
{file_count} file(s).

**First action:** run `wade review-pr-comments-session check` to confirm you're
in a worktree, then `wade review-pr-comments-session fetch {issue_number}` to
read every unresolved comment.
