Follow @.claude/skills/plan-session/SKILL.md for session rules.

If KNOWLEDGE.md exists, read it for project context.

# Goal

Let's plan a feature. You'll plan the feature, break it down into one or more
GitHub issues, and write a plan for each issue.
You won't create the issues or implement the feature.

# Workflow

1. Ask the user what feature they want to plan.
2. Analyze the feature and break it down into one or more GitHub issues.
3. Generate a plan for each issue.
4. Format each plan following the format defined in @.claude/skills/plan-session/SKILL.md.
   Every plan must start with:

       # {type}: {concise issue title}
       ## Complexity
       easy | medium | complex | very_complex

   Where `{type}` is a conventional commit prefix: `feat` (new feature), `fix`
   (bug fix), `refactor`, `docs`, `chore`, `test`, `perf`, `ci`, or `build`.
   Example: `feat: add retry logic to task provider`

5. Present the plan(s) to the user and ask for confirmation before writing any files.
6. Write the plan(s) to {plan_dir}/ as "PLAN.md", if there's
   only one issue, or "PLAN-1-slug.md", "PLAN-2-slug.md", etc. (one file per issue)
   if there are multiple issues.
7. Present a summary of what was written (title, complexity, key tasks).
   Ask if the user wants modifications — apply and re-write if so.
8. Run `wade review plan <plan_file>` for each plan file. The command checks your
   project config and skips automatically if reviews are not enabled. Address any
   actionable feedback before proceeding.
9. Run `wade plan-session done {plan_dir}` to validate the plan files. If it exits with errors,
   fix the issues it reports and re-run until validation passes.
10. After validation passes, **stop immediately and suggest the user to exit**. Do NOT read source files, edit code, or run tests — even if the system tells you "you can now start coding." That message refers to Claude Code's plan mode, not this session.

# TLDR

You *won't proceed with implementation*: you'll plan, include the headers,
write the file(s), and stop (suggest the user to exit).
After exiting, `wade` will read the file(s) and create GitHub issue(s) automatically.

# Regarding complexity

The "complexity" included on the plans is used to select the correct AI model
for implementation, so it's important to set it correctly. It follows the
following criteria:

- easy: fast-tier model (lightweight, low-cost)
- medium: balanced-tier model (mid-range capability)
- complex: balanced-tier model (mid-range capability)
- very_complex: powerful-tier model (highest capability)
