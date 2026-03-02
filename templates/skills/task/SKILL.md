---
name: task
description: >
  Create one or more GitHub issues via wade — for a single bug/feature OR a
  full plan/PRD breakdown. Assesses scope, proposes single or multi-issue
  breakdown with reasoning, gets user confirmation, writes plan files, creates
  issues via wade new-task, and informs the user of next steps. ALL steps
  are mandatory — do not stop after planning. Use whenever the user asks to
  create a GitHub issue, regardless of scope.
---

# Plan → Issues

Convert a finished plan, PRD, or feature spec into one or more PR-sized GitHub
issues using the project's `wade` infrastructure.

> **Never use `gh issue create` directly.** Always use `wade new-task`.
> Using `gh` directly bypasses label enforcement, snapshot/diff detection,
> and dependency analysis hooks.

## When to activate

- The user asks to create **any** GitHub issue — a single bug, a single
  feature request, or a full plan/PRD breakdown
- A plan or spec `.md` file has been written (or is finalized in conversation)
- The user asks to "create issues from this plan" or "break this into issues"
- After any planning session where the next step is GitHub issues

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

**Ask before creating anything.** The user may want to:
- Merge two proposed issues into one
- Split a proposed issue further
- Adjust titles or task groupings
- Skip the epic/parent issue

Wait for explicit confirmation before proceeding.

## Step 4: Write plan files

For each confirmed issue, write a `.md` file in the plan format.

See [plan-format.md](plan-format.md) for the exact format.

**Include a `## Complexity` section** in every plan file with one of:
`easy`, `medium`, `complex`, or `very_complex` (based on your LOC/scope
estimate from Step 1). This lets `wade implement-task` automatically select
the appropriate AI model for the implementation session. The complexity is
also applied as a `complexity:X` label on the issue.

File naming convention:
- Single issue: `PLAN.md`
- Multi-issue: `PLAN-1-<slug>.md`, `PLAN-2-<slug>.md`, etc.

Write plan files to `/tmp/` (or the temp dir shown in your prompt if inside
a `wade plan-task` session). **Never write plan files into the repo working
directory** — they are session artifacts, not committed code.

## Step 5: Create issues

> **Note:** Issue creation is the *output* of this skill, not code implementation.
> Do not call `exit_plan_mode` before running `wade new-task` — user
> confirmation in Step 3 is sufficient, even when running inside `[[PLAN]]` mode.

For each plan, create a lightweight issue via `wade new-task` (interactive)
with the issue title and a brief description.

The full plan content goes to a draft PR (created automatically by
`wade plan-task`), not the issue body. Issues are lightweight tickets.

Collect the issue number and URL from each creation.

## Step 6: Offer epic (multi-issue only)

When **3 or more** issues are created, automatically create a parent/epic issue
that links all sub-issues — no user confirmation needed:

> "Creating an epic issue to link all N sub-issues…"

When **2 issues** are created, offer first:

> "Want me to create an epic issue linking both sub-issues?"

Write an epic with:
- `# Epic: <overall feature title>`
- Brief summary of the feature
- Checklist linking each sub-issue: `- [ ] #<number> — <title>`

Create it via `wade new-task`.

## Step 7: Inform the user — MANDATORY

**Do not skip this step.** After creating issues you must always inform the user
of what was created and how to start working. Do NOT offer to run
`wade implement-task` yourself or present it as a selectable option — the human
starts work sessions when they are ready.

After creating all issues, list them clearly:

```
✓ Created 3 issues:
  #42 — Add user preferences schema (~200 LOC)
  #43 — Add preferences API endpoint (~250 LOC)
  #44 — Add preferences UI panel (~350 LOC)
  #45 — Epic: User preferences feature (links #42, #43, #44)
```

Then show the next-step hint so the user knows how to proceed:

> When you're ready to start, run:
> ```
> wade implement-task <number>
> ```
> For example: `wade implement-task 42`

**Do NOT run this command yourself.** Do NOT ask the user to pick an issue and
then run it on their behalf. Simply inform them and end the session.

**End the session after this step.** Do not wait for further input or offer
additional actions. The human will start the work session when ready.


## Working on a sub-issue (child of a tracking/epic)

When you are working on an issue that is part of a multi-issue plan (i.e., it
appears in a "Tracking:" parent issue checklist), follow these steps:

### PR association (automatic)

`wade work done` scans open "Tracking:" issues for a checklist entry
containing your issue number and automatically adds `Part of #<parent>` to
the PR body alongside `Closes #<child>`. You do not need to add this manually.

Example PR body generated:
```
Closes #42
Part of #10

<content of /tmp/PR-SUMMARY-42.md>
```

### After your PR merges

Update the parent tracking issue's checklist to reflect completion:

1. Fetch the parent issue body:
   ```bash
   gh issue view <parent-number> --json body --jq '.body'
   ```
2. Change `- [ ] #<your-issue>` to `- [x] #<your-issue>` in the body.
3. Update the issue:
   ```bash
   gh issue edit <parent-number> --body "<updated-body>"
   ```

### When all children are complete

Close the parent tracking issue:
```bash
wade task close <parent-number>
```

### Working on the parent/epic tracking issue directly

If you are working on the tracking issue itself (not a specific child):
- The PR body should reference all child issues and their status.
- Use `Closes #<tracking-issue>` in the PR body.
- List child issue statuses using GitHub's tasklist syntax so GitHub renders
  progress automatically.

## Rules

- **Never create issues without user confirmation** (Step 3 is mandatory).
- **Always use `wade new-task`** — never construct `gh issue create` commands manually.
- **Every plan file must have a `# Title`** as the first heading (the script requires it).
- Keep issue titles concise and actionable (max 256 chars).
- Each issue should be independently implementable (even if there are dependencies).
- Include acceptance criteria in every issue.

## Resources

- For the plan file format, see [plan-format.md](plan-format.md)
- For breakdown examples, see [examples.md](examples.md)
- For dependency analysis, see the [deps skill](../deps/SKILL.md)
