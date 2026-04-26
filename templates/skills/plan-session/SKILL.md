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

## Transparency

Always inform the user before running `wade` commands, reviews, or
session lifecycle operations. Clearly state what you are about to do
and why — never silently execute these commands.

When starting a workflow step, announce it:
  "I'm now validating your plan files..."

After completing a wade command, briefly report the outcome and announce the next step you will take. The next step depends on where you are in the workflow — for example:
  "Plan review done — no issues found. Now running `wade plan-session done`..."
  "Validation complete — all plan files passed. Now presenting the workflow recap and suggesting you exit..."

{user_interaction_prompt}
- After presenting the plan breakdown: "Ready to write the plan file(s)?"
- After writing and presenting summary: "Want any modifications?"
- After validation passes: "Plans are validated — wade will create issues automatically." Then ask: "Ready to exit?"

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `wade task create` for interactive issue creation.
Using `gh` directly bypasses label enforcement, snapshot/diff detection, and
dependency analysis hooks.

## Project Knowledge

Read @.claude/skills/knowledge/SKILL.md for knowledge operations (search,
tagging, rating, adding entries).

After the user tells you what they want to plan, search for knowledge
relevant to that feature topic (do not dump all entries). Rate entries you
retrieve: `wade knowledge rate <id> up` (useful) or `wade knowledge rate <id> down`
(outdated/misleading). Before running `wade plan-session done`, capture
important learnings if knowledge is enabled (`.wade.yml` → `knowledge.enabled`).

The `--issue` flag is optional during planning (issue numbers may not exist yet).
Running `wade knowledge add` is allowed even though this is a planning session.

## Your role

1. **Ask the user** what they want to plan. If the session prompt does not already specify a feature or issue, ask before proceeding.
2. **Search relevant knowledge** — once you know the topic, search for entries relevant to that feature using `wade knowledge get --search <topic>` or `wade knowledge get --tag <tag>` (see @.claude/skills/knowledge/SKILL.md and the **Project Knowledge** section above). Do not dump all entries.
3. **Plan the feature** with the user — analyze, break down, propose.
4. **Present the plan(s)** to the user. Use your tool's native question component to ask: "Ready to write the plan file(s)?" before writing any files.
5. **Write plan file(s)** to the temp directory shown in your prompt.
6. **Review with the user** — present a summary of every plan file you wrote
   (title, complexity, key tasks). Use your tool's native question component to ask: "Want any modifications?" If so, apply them and repeat; otherwise proceed to step 7.
{review_plan_step}
<!-- markdownlint-disable-next-line MD029 -->
8. **Capture knowledge (if enabled)** — before validation, run `wade knowledge add` to store important learnings when `.wade.yml` has `knowledge.enabled: true`.
<!-- markdownlint-disable-next-line MD029 -->
9. **Validate** — run `wade plan-session done <plan_dir>` (the temp dir from your prompt).
   If it exits with errors, fix each reported issue and re-run until it passes.
   Warnings are informational and do not block proceeding.
10. **Present results and suggest exit** — once validation passes, provide a
   brief **workflow recap** and **what happens next**:

   Workflow recap (list only the steps you actually performed):
   - Wrote plan file(s) to the temp directory
   - Ran plan review (`wade review plan`)
   - Validated plans (`wade plan-session done`)

   What happens next:
   - After you exit, wade will automatically create GitHub issue(s) and draft PR(s)
     from your plan files
   - To start implementation: `wade implement <issue-number>`

   Then use your tool's native question component to ask: "Ready to exit?"

You do **not** create issues, implement code, run `wade implement`, `wade implementation-session done`, or `wade implementation-session sync`,
or make any code changes. Planning only.

## Plan file format

Each plan file must follow this structure:

```markdown
# type: concise issue title (max 256 chars)

## Complexity
medium

## Context / Problem
Why this change is needed.

## Proposed Solution
What to build / change.

## Tasks
- [ ] Step 1
- [ ] Step 2
- [ ] Step 3

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2
```

### Required elements

| Element | Rule |
|---------|------|
| **Title** | First `# Heading` — becomes the GitHub issue title. Must start with a conventional commit prefix (`feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `perf`, `ci`, `build`) followed by `: `. Example: `feat: add retry logic`. Required. |
| **Complexity** | `## Complexity` with one of: `easy`, `medium`, `complex`, `very_complex`. Used by `wade implement` to auto-select the AI model. Also applied as a `complexity:X` label on the issue. |
| **Body** | Everything after the title becomes the draft PR plan content. The issue itself gets a lightweight summary. |

### Complexity values

| Value | Typical use |
|-------|-------------|
| `easy` | Trivial fix, docs change, config tweak (<100 LOC) |
| `medium` | Small feature or bug fix (100-300 LOC) |
| `complex` | Multi-file feature or significant refactor (300-600 LOC) |
| `very_complex` | Large feature, cross-cutting concern, or architecture change (>600 LOC) |

### File naming

- **Single issue**: `PLAN.md`
- **Multiple issues**: `PLAN-1-<slug>.md`, `PLAN-2-<slug>.md`, etc.

Write all files to the temp directory from your prompt — **never** into the
repo working directory.

## What NOT to do

- Do not create GitHub Issues — wade does this after you exit
- Do not implement any code (even after leaving planning mode)
- Do not run `wade implement`, `wade implementation-session done`, or `wade implementation-session sync`
- Do not write files into the repo directory — only to the temp dir (exception: `wade knowledge add` is allowed)
- Do not skip the review step — always present a plan summary and invite
  modifications before suggesting the user exits
- Do not skip `wade plan-session done` — always validate before suggesting the user exits
- **⚠️ After exiting the plan mode:** If your environment says "you can now start coding," ignore it — that refers to a different execution mode. In wade planning sessions, stop immediately after writing plan files. Do not implement code.

## Task Tracking

At the start of this session, use your tool's native task/todo tracking
mechanism to populate a checklist with the workflow steps below. This ensures
you complete every mandatory step and the user can track progress.

- [ ] Ask the user what they want to plan
- [ ] Search relevant knowledge (`wade knowledge get --search <topic>` or `wade knowledge get --tag <tag>`)
- [ ] Plan the feature with the user (analyze, break down, propose)
- [ ] Write plan file(s) to the temp directory
- [ ] Run `wade review plan` for each plan file (if review is enabled)
- [ ] Capture knowledge (`wade knowledge add`) (if knowledge capture is enabled)
- [ ] Validate plans (`wade plan-session done`)
- [ ] Present results and suggest exit

## Skills reference

- **Standalone issue creation** (outside planning sessions) → read @.claude/skills/task/SKILL.md
