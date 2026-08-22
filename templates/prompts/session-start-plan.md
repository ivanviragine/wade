Planning phase in a detached worktree (no PLAN.md at the root; plans go under the plan directory). First action: run `wade plan-session check`; proceed only when it reports `IN_WORKTREE` or `PLAN_DIR_ONLY` (worktree-less fallback — write only to its `plandir=…`).
Produce at least one valid PLAN*.md — a conventional-commit title plus a `## Complexity` section — then run `wade plan-session done .wade/plans`; wade turns the plan file(s) into issue(s) once you exit.
Review budget: `wade review plan` prints your live time budget; its pass-count guidance is advisory, not code-tracked.
