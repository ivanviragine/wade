## Git Workflow

**First action every managed session** — read `.wade/session/WORKFLOW.md` and
the exact active WORK skill files it lists. The workflow owns lifecycle and
safety; replaceable skills own methodology only.

Critical rules you must always follow:

1. Never create GitHub Issues via `gh issue create` — use `wade task create`
2. Never create PRs manually (`gh pr create`) or push branches directly — use
   the session's `done` command (`wade implementation-session done` or
   `wade review-pr-comments-session done`)
