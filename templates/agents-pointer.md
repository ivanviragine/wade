## Git Workflow

**First action every session** — read @.claude/skills/workflow/SKILL.md for
full rules. The critical rules you must always follow:

1. Run `ghaiw check` first — **never edit source files in the main checkout, even before committing**; only planning operations (creating issues, writing plan files to `/tmp`) are allowed from main
2. Never create PRs manually (`gh pr create`) or push branches directly. `ghaiw work done` is the only way to finalize work — if it fails, debug and fix the error; do NOT bypass it
3. To finalize work: `ghaiw work sync` then `ghaiw work done`
4. Never create GitHub Issues via `gh issue create` — use `ghaiw task create` or read @.claude/skills/task/SKILL.md
