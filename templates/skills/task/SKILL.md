---
name: task
description: >
  Create one or more tasks via `wade task create` — for a single bug/feature OR
  a full plan/PRD breakdown. This is the STANDALONE task-creation workflow, used
  whenever the user directly asks to create a task/issue outside a `wade plan`
  session. Assesses scope, proposes a single- or multi-issue breakdown with
  reasoning, gets user confirmation, writes the plan content, creates the tasks
  via `wade task create`, and informs the user of next steps. ALL steps are
  mandatory — do not stop after planning. Use whenever the user asks to create a
  task/issue, regardless of scope.
---

# Create Tasks

Convert a finished plan, PRD, or feature spec into one or more PR-sized tasks
(GitHub Issues, or whatever task provider the project configures) using the
project's `wade` infrastructure. Task creation itself is provider-neutral;
**dependency-parent creation is not** — see the scope note in Step 6.

> **This is standalone task creation — not a planning session.** In a
> `wade plan` session you do **not** run `wade task create`: you write plan
> files and exit, and wade creates the tasks (and draft PRs) after you leave
> (see @.claude/skills/plan-session/SKILL.md). Use this skill only when the user
> asks to create tasks directly.

> **Never use `gh issue create` directly.** Always use `wade task create`.
> Using `gh` directly bypasses conventional-title enforcement, the configured
> issue label, and the task-provider abstraction.

## When to activate

- The user asks to create **any** task/issue — a single bug, a single
  feature request, or a full plan/PRD breakdown
- A plan or spec `.md` file has been written (or is finalized in conversation)
- The user asks to "create issues from this plan" or "break this into issues"

Do **not** activate inside a `wade plan` planning session — that session only
writes plan files and exits; wade creates the tasks afterward.

> **Single issues are in scope.** Do not skip this skill just because there
> is only one issue to create. The skill handles single-issue creation
> (Step 1 assess → propose single issue → confirm → create) exactly like
> multi-issue plans — it just skips the multi-issue steps.

## Step 1: Assess scope

Read the plan and estimate the implementation size:

- **Lines of code** — rough total across all files
- **Number of concerns** — distinct areas (new endpoint, schema change, UI, tests, docs)
- **File count** — how many files will be touched

### PR-size heuristics

| Metric | Single issue | Multi-issue split |
|--------|-------------|-------------------|
| LOC | ~300–500 | >500 |
| Concerns | 1–2 related | 3+ distinct areas |
| Files | ≤10 | >10 across layers |
| Review time | <1 hour | >1 hour |

## Step 2: Propose breakdown

Present the assessment to the user with clear reasoning:

**Single issue** — when the plan fits in one PR:
> "This plan is ~350 LOC touching 6 files, all related to [concern].
> I recommend keeping it as **1 issue**."

**Multi-issue** — when the plan should be split:
> "This plan spans ~800 LOC across 3 distinct areas: [A], [B], [C].
> I recommend splitting into **3 issues**:"
> 1. Issue title — scope description (~LOC, files)
> 2. Issue title — scope description (~LOC, files)
> 3. Issue title — scope description (~LOC, files)

Always include:
- Estimated LOC per issue
- Which tasks from the plan map to which issue
- Dependencies between issues (if any)

## Step 3: Get user confirmation

**Ask before creating anything.** Present a native dialog whose first option is
recommended and whose labels name the next step
(@.claude/skills/task/reference/session-summary-format.md):

- `Create the issue(s) now (recommended)`
- `Adjust first — merge, split, or retitle`

The user may want to merge two proposed issues into one, split one further,
adjust titles or task groupings, or skip the epic/parent issue. If they choose to
adjust, apply the changes and re-present. Wait for explicit confirmation before
proceeding.

## Step 4: Write plan files

For each confirmed issue, write a `.md` file in the plan format. Its content
becomes the task body you pass to `wade task create` in Step 5 — and that body
seeds `PLAN.md` when the issue is later implemented with `wade implement`.

See [plan-format.md](plan-format.md) for the exact format.

**Every plan file needs a conventional-commit `# Title` and a `## Complexity`
section** with one of `easy`, `medium`, `complex`, or `very_complex` (based on
your LOC/scope estimate from Step 1). Complexity lets `wade implement`
auto-select the AI model and is applied as a `complexity:X` label on the issue.

File naming convention:
- Single issue: `PLAN.md`
- Multi-issue: `PLAN-1-<slug>.md`, `PLAN-2-<slug>.md`, etc.

Write plan files to the worktree root (or your current working directory).
**Never write plan files into the repo's main checkout** — they are working
artifacts, not committed code.

## Step 5: Create tasks

> **Note:** Task creation is the *output* of this skill, not code implementation.
> Do not call `exit_plan_mode` before running `wade task create` — user
> confirmation in Step 3 is sufficient, even when running inside `[[PLAN]]` mode.

For each plan, create a task **non-interactively** — installed agents run in a
non-TTY shell, where bare `wade task create` cannot read a title or body (it
exits with "Title is required" and discards the body). Pass the plan-file title
and body explicitly:

```bash
wade task create --title "<plan-file title>" --body-file PLAN.md
```

`--body-file` reads the whole plan file as the issue body (`--body "<text>"`
takes a short inline body instead). `wade task create` enforces the
conventional-commit title and applies the configured issue label.

The task body you provide seeds `PLAN.md` when the issue is later implemented, so
pass the full plan file — include enough to work from.

> **Contrast with `wade plan`:** when a project uses `wade plan` instead, wade
> keeps the issue lightweight and puts the full plan in a draft PR it creates
> automatically after the planning session exits. That path does not use this
> skill.

Collect the issue number and URL from each creation.

## Step 6: Create the parent issue (multi-issue only)

A multi-issue set gets **exactly one** parent — never two competing checklists.

> **Scope note:** WADE's dependency **and parent-linkage** automation is
> numeric-ID only. `wade task deps` (CLI + dependency-graph parser) assumes
> digits, and parent detection (`find_parent_issue` plus the `- [ ] #<number>`
> checklist parser behind `done` and batch lifecycle flows) matches only
> `#<digits>` refs. Providers with opaque task IDs (e.g. ClickUp —
> `provider.name: clickup` in `.wade.yml`, IDs like `abc123`) can use neither.
> On such a project, skip `wade task deps` and **use the epic path below even
> when the issues have dependencies** — but the epic is a **human-readable
> tracking doc only**: wade will not auto-associate its children, tick its
> checklist on merge, or auto-close it. Record the dependency order and the
> sub-task list in the epic body for humans to follow.

Choose the parent by whether the issues have dependencies (GitHub/numeric-ID
providers only — see scope note above):

**Issues with dependencies → run `wade task deps <issue-numbers>`.** Pass the
issue numbers explicitly — bare `wade task deps` only falls back to an
interactive picker, which requires a TTY a running agent doesn't have. This
matches the `wade plan` lifecycle: it writes cross-references onto each issue
**and creates a tracking issue** (execution plan + dependency graph) that
serves as the parent. The tracking issue *is* the parent — do **not** also
create an epic. See [examples.md](examples.md) for the command and its output.

**Independent issues (no dependencies) → create an epic** instead of running
`wade task deps`. When **3 or more** independent issues are created, create the
epic automatically — no user confirmation needed:

> "Creating an epic issue to link all N sub-issues…"

When **2 issues** are created, offer first via a native dialog:

- `Create the epic (recommended)` — links both sub-issues
- `Skip the epic`

Write an epic with:
- `# feat(epic): <overall feature title>` — the title must be conventional-commit
  format like any other issue (`wade task create` enforces it); `feat(epic)`
  keeps it self-identifying as the parent
- Brief summary of the feature
- Checklist linking each sub-issue: `- [ ] #<number> — <title>` (the `#<number>`
  form is what drives wade's parent automation on numeric-ID providers; on an
  opaque-ID provider it is a plain human-readable list — see the scope note above)

Create it non-interactively — write the epic body to a file first:

```bash
wade task create --title "feat(epic): <overall feature title>" --body-file EPIC.md
```

## Step 7: Inform the user — MANDATORY

**Do not skip this step.** After creating issues you must always inform the user
of what was created and how to start working. Do NOT offer to run
`wade implement` yourself or present it as a selectable option — the human
starts work sessions when they are ready.

After creating all issues, end with the shared final-summary skeleton
(@.claude/skills/task/reference/session-summary-format.md) — a step-status line,
the issue handles (number + title + URL), an attention line, and a bold `Next:`
line:

```text
✅ Plan file(s) written · ✅ Issues created · ✅ Epic linked
  #42 — feat: add user preferences schema (~200 LOC) — https://github.com/…/issues/42
  #43 — feat: add preferences API endpoint (~250 LOC) — https://github.com/…/issues/43
  #44 — feat: add preferences UI panel (~350 LOC) — https://github.com/…/issues/44
  #45 — feat(epic): user preferences feature (links #42, #43, #44) — https://github.com/…/issues/45
✅ Nothing needs your attention.
Next: run `wade implement <number>` when you're ready — e.g. `wade implement 42`.
```

For a **single issue** (no epic) the status line is `✅ Plan file written ·
✅ Issue created · ⏭️ Epic (single issue)` — mark the epic step ⏭️, never ✅,
since it did not run. If a creation failed or needs follow-up, use ⚠️/❌ on that
step and add the reason to the attention line.

The `Next:` line **names** the command; it does not run it — see the rule below.

**Do NOT run this command yourself.** Do NOT ask the user to pick an issue and
then run it on their behalf. Simply inform them and end the session.

**End the session after this step.** Do not wait for further input or offer
additional actions. The human will start the work session when ready.


## Working on sub-issues

For implementation details when working on child issues of a tracking/epic
(PR association, post-merge checklist updates, closing parent issues),
see @.claude/skills/implementation-session/SKILL.md.

## Rules

- **Never create issues without user confirmation** (Step 3 is mandatory).
- **Always use `wade task create`** — never construct `gh issue create` commands manually.
- **Every plan file must have a `# Title`** as the first heading (the script requires it).
- **Issue titles must be [Conventional Commits](https://www.conventionalcommits.org/)
  format** (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `style:`, `perf:`,
  `test:`, `ci:`, `build:`, `revert:`, `update:`). The PR title is derived from
  the issue title verbatim, so a non-conventional title fails the `PR Title Lint`
  CI check. `wade task create` **enforces this in code** — a non-conventional
  `--title` is rejected and interactive create re-prompts. Choose the prefix that
  matches the change; wade never guesses it for you.
- Keep issue titles concise and actionable (max 256 chars).
- Each issue should be independently implementable (even if there are dependencies).
- Include acceptance criteria in every issue.

## Resources

- For the plan file format, see [plan-format.md](plan-format.md)
- For breakdown examples, see [examples.md](examples.md)
- For dependency analysis, see the [deps skill](../deps/SKILL.md)
