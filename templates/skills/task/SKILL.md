---
name: task
description: >
  Create one or more GitHub issues via ghaiw — for a single bug/feature OR a
  full plan/PRD breakdown. Assesses scope, proposes single or multi-issue
  breakdown with reasoning, gets user confirmation, writes plan files, creates
  issues via ghaiw task create, and informs the user of next steps. ALL steps
  are mandatory — do not stop after planning. Use whenever the user asks to
  create a GitHub issue, regardless of scope.
---

# Plan → Issues

Convert a finished plan, PRD, or feature spec into one or more PR-sized GitHub
issues using the project's `ghaiw` infrastructure.

> **Never use `gh issue create` directly.** Always use `ghaiw task create` or
> `ghaiw task create --plan-file`. Using `gh` directly bypasses label
> enforcement, snapshot/diff detection, and dependency analysis hooks.

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

## Inside a `ghaiw task plan` session

If you were launched by `ghaiw task plan`, you are in a **planning session**.

**Do not create GitHub Issues yourself** — ghaiw handles that automatically
after you exit.

Your job in a planning session:
1. Plan the feature with the user
2. Write your plan as Markdown file(s) to the directory shown in your
   clipboard/prompt (format: `# Title`, `## Complexity`, `## Tasks`, etc.)
3. Exit — ghaiw reads the files and creates GitHub Issues automatically

The steps below describe the **manual issue-creation workflow**, used when
creating issues outside of a `ghaiw task plan` session. Only follow Steps 1-7
when not inside a planning session.

## Step 1: Assess scope

Read the plan and estimate the implementation size:

- **Lines of code** — rough total across all files
- **Number of concerns** — distinct areas (new endpoint, schema change, UI, tests, docs)
- **File count** — how many files will be touched

### PR-size heuristics

| Metric | Single issue | Multi-issue split |
|--------|-------------|-------------------|
| LOC | ~300-500 | >500 |
| Concerns | 1-2 related | 3+ distinct areas |
| Files | <=10 | >10 across layers |
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

For each confirmed issue, write a `.md` file in the format that
`ghaiw task create --plan-file` expects.

See [plan-format.md](plan-format.md) for the exact format.

**Include a `## Complexity` section** in every plan file with one of:
`easy`, `medium`, `complex`, or `very_complex` (based on your LOC/scope
estimate from Step 1). This lets `ghaiw work start` automatically select
the appropriate AI model for the implementation session.

File naming convention:
- Single issue: `plan.md`
- Multi-issue: `plan-1-<slug>.md`, `plan-2-<slug>.md`, etc.

Write plan files to `/tmp/` (or the temp dir shown in your clipboard if inside
a `ghaiw task plan` session). **Never write plan files into the repo working
directory** — they are session artifacts, not committed code.

## Step 5: Create issues

> **Note:** Issue creation is the *output* of this skill, not code implementation.
> Do not call `exit_plan_mode` before running `ghaiw task create` — user
> confirmation in Step 3 is sufficient, even when running inside `[[PLAN]]` mode.

For each plan file, run:

```bash
ghaiw task create --plan-file <path-to-plan-file> --no-start
```

The `--no-start` flag suppresses the interactive "start working?" prompt — the
agent handles that decision itself in Step 7.

The script handles:
- Title extraction from the `# Heading`
- Body formatting
- Label from `.ghaiw.yml` config

Collect the issue number and URL from each creation.

## Step 5.25: Plan summary token usage (`task plan` flow)

When issues are created via `ghaiw task plan`, the shell script automatically
annotates each new issue body with:

- `## Plan Summary`
- `### Usage`

If the AI CLI reports session token usage, the summary includes the total and,
when available, session `input` / `output` / `cached` counts.
If the AI CLI reports per-model usage rows, the summary also includes a
`### Model Breakdown` table.
For multi-issue plans, per-issue token values are estimated proportionally
using each issue body's `wc -l` line count.

If the AI CLI reports premium-request estimates (for example, Copilot's
"Total usage est"), that estimate is also included in the summary.

If token usage cannot be parsed from the AI CLI transcript, the summary is
still written and marked as unavailable.

## Step 5.5: Dependency analysis (multi-issue only)

When multiple issues are created, `ghaiw task plan` automatically runs
dependency analysis via `ghaiw task deps`. The shell script handles this —
**no agent action is needed**. The script will:

1. Launch an AI session to analyze dependencies between the new issues
2. Generate a Mermaid dependency graph
3. Update each issue's body with "Depends on" / "Blocks" references

## Step 6: Offer epic (multi-issue only)

When **3 or more** issues are created, automatically create a parent/epic issue
that links all sub-issues — no user confirmation needed:

> "Creating an epic issue to link all N sub-issues..."

When **2 issues** are created, offer first:

> "Want me to create an epic issue linking both sub-issues?"

Write an epic `.md` file with:
- `# Epic: <overall feature title>`
- Brief summary of the feature
- Checklist linking each sub-issue: `- [ ] #<number> — <title>`

Create it via `ghaiw task create --plan-file <epic-file>`.

## Step 7: Inform the user — MANDATORY

**Do not skip this step.** After creating issues you must always inform the user
of what was created and how to start working. Do NOT offer to run
`ghaiw work start` yourself or present it as a selectable option — the human
starts work sessions when they are ready.

After creating all issues, list them clearly:

```
Created 3 issues:
  #42 — Add user preferences schema (~200 LOC)
  #43 — Add preferences API endpoint (~250 LOC)
  #44 — Add preferences UI panel (~350 LOC)
  #45 — Epic: User preferences feature (links #42, #43, #44)
```

Then show the next-step hint so the user knows how to proceed:

> When you're ready to start, run:
> ```
> ghaiw work start <number>
> ```
> For example: `ghaiw work start 42`

**Do NOT run this command yourself.** Do NOT ask the user to pick an issue and
then run it on their behalf. Simply inform them and end the session.

**End the session after this step.** Do not wait for further input or offer
additional actions. The human will start the work session when ready.

**Shortcut:** If the user asked to create issues from a plan file, they can also
skip issue creation entirely and run `ghaiw work start <plan-file>` directly —
this creates the issue **and** starts the work session in one step.


## Working on a sub-issue (child of a tracking/epic)

When you are working on an issue that is part of a multi-issue plan (i.e., it
appears in a "Tracking:" parent issue checklist), follow these steps:

### PR association (automatic)

`ghaiw work done` scans open "Tracking:" issues for a checklist entry
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
ghaiw task close <parent-number>
```

### Working on the parent/epic tracking issue directly

If you are working on the tracking issue itself (not a specific child):
- The PR body should reference all child issues and their status.
- Use `Closes #<tracking-issue>` in the PR body.
- List child issue statuses using GitHub's tasklist syntax so GitHub renders
  progress automatically.

## Rules

- **Never create issues without user confirmation** (Step 3 is mandatory).
- **Always use `ghaiw task create --plan-file`** — never construct `gh issue create` commands manually.
- **Every plan file must have a `# Title`** as the first heading (the script requires it).
- Keep issue titles concise and actionable (max 256 chars).
- Each issue should be independently implementable (even if there are dependencies).
- Include acceptance criteria in every issue.

## Resources

- For the plan file format, see [plan-format.md](plan-format.md)
- For breakdown examples, see [examples.md](examples.md)
- For dependency analysis, see the [deps skill](../deps/SKILL.md)
