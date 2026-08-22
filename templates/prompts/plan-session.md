Follow @.claude/skills/plan-session/SKILL.md for session rules — build a todo
list from its workflow steps before starting work.

**First action:** run `wade plan-session check`. Do not write a plan or
knowledge vote unless it reports `IN_WORKTREE` or `PLAN_DIR_ONLY` (write only
to its `plandir=…`); follow its specific `reason=…` remediation rather than
disabling your sandbox broadly.

# Goal

Plan a feature: break it into one or more tasks and write a plan file for
each to {plan_dir}/ (one file per task). You won't create the tasks or
implement the feature — after you exit, wade reads the plan files and creates the
task(s) and draft PR(s) automatically.
