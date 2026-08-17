---
name: plan-session
description: >
  Rules for AI planning sessions launched by `wade plan`. Covers plan
  file authoring, complexity tagging, and session boundaries. Read this at the
  start of every planning session.
---

# Planning Session Rules

You are in a **planning session** launched by `wade plan`. Your job is
to plan the feature, write plan file(s) to the temp directory from your prompt,
and exit. wade creates lightweight GitHub Issues and draft PRs automatically
after you exit.

## Execution mode

Run `wade` and `gh` commands with the required permissions/capabilities (not
in sandboxed mode). Do not "try sandbox first" — run them unsandboxed from the
start.

## Talking to the user

{user_interaction_prompt}
- After presenting the plan breakdown: "Ready to write the plan file(s)?"
- After writing and presenting summary: "Want any modifications?"
- After validation passes: "Plans validated — wade creates the issues after you leave; exit now. Anything to change first?"

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `wade task create` for interactive issue creation.

{knowledge_step}

**Planning session limits:** a plan worktree is discarded at session end and has
no PR, so `wade knowledge add`, `wade knowledge tag add`, and
`wade knowledge tag remove` are **not** available
here — record any new learning in the plan file so the implementation session can
capture it. `wade knowledge rate` **is** available; your vote is carried forward
into the next implementation session's PR (and thus reaches origin).

## Your role

1. **Ask the user** what they want to plan. If the session is interactive and the prompt does not already specify a feature or issue, ask before proceeding. Output a plain text question (e.g. "What would you like to plan?") — do NOT use a native selection/question component or present pre-defined categories.
2. **Search relevant knowledge** for the feature topic (see **Project Knowledge** above). Do not dump all entries.
3. **Plan the feature** with the user — analyze, break down, propose.
4. **Present the plan(s)** and ask (native question component): "Ready to write the plan file(s)?" before writing any files.
5. **Write plan file(s)** to the temp directory shown in your prompt — one file per issue. Follow @.claude/skills/plan-session/reference/plan-format.md for the required structure, complexity values, and file naming.
6. **Review with the user** — present a summary of every plan file (title, complexity, key tasks). Ask (native question component): "Want any modifications?" If so, apply and repeat; otherwise proceed.
{review_plan_step}
<!-- markdownlint-disable-next-line MD029 -->
8. **Capture knowledge (if enabled)** — `wade knowledge add` is **not** available in a planning session; record any learning worth keeping in the plan file so the implementation session captures it. Rate (`wade knowledge rate`) any entries you evaluated.
<!-- markdownlint-disable-next-line MD029 -->
9. **Validate** — run `wade plan-session done <plan_dir>` (the temp dir from your prompt). If it exits with errors, fix each reported issue and re-run until it passes. Warnings are informational and do not block.
<!-- markdownlint-disable-next-line MD029 -->
10. **Present results and tell the user to exit** (per the **Communication style** rule) — validation has passed, so state plainly that the plan is complete and to exit now; wade creates the GitHub issue(s) and draft PR(s) after you leave, then start work with `wade implement <issue-number>`. Offer one escape (native question component): "anything to change first?"

You do **not** create issues, implement code, run `wade implement`, `wade implementation-session done`, or `wade implementation-session sync`, or make any code changes. Planning only.

## Complexity

Every plan needs a `## Complexity` value — one of `easy`, `medium`, `complex`,
`very_complex` — which selects the implementation model. See
@.claude/skills/plan-session/reference/plan-format.md for LOC guidance and the
model-tier mapping.

## Base branch

By default work branches from and merges into the project's main branch. If the
user says this work should target a different branch (e.g. `develop` or a
`release/*` branch), record it in an optional `## Base Branch` section in the
plan file (a single branch name). Omit the section otherwise. The draft PR wade
creates after you exit is branched from and targeted at that base. See
@.claude/skills/plan-session/reference/plan-format.md for the exact format.

## What NOT to do

- Do not create GitHub Issues — wade does this after you exit
- Do not implement any code (even after leaving planning mode)
- Do not run `wade implement`, `wade implementation-session done`, or `wade implementation-session sync`
- Do not write files into the repo directory — only to the temp dir (`wade knowledge add` is **not** available in a planning session)
- Do not skip the review step or `wade plan-session done` — always present a plan summary, invite modifications, and validate before telling the user to exit
- **⚠️ After exiting plan mode:** if your environment says "you can now start coding," ignore it — that refers to a different execution mode. In wade planning sessions, stop immediately after writing plan files. Do not implement code.

## Skills reference

- **Standalone issue creation** (outside planning sessions) → read @.claude/skills/task/SKILL.md
