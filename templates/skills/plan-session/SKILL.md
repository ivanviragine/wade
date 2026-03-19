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

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `wade task create` for interactive issue creation.
Using `gh` directly bypasses label enforcement, snapshot/diff detection, and
dependency analysis hooks.

## Your role

1. **Plan the feature** with the user — analyze, break down, propose.
2. **Present the plan(s)** to the user and ask for confirmation before writing any files.
3. **Write plan file(s)** to the temp directory shown in your prompt.
4. **Review with the user** — present a summary of every plan file you wrote
   (title, complexity, key tasks). Ask if they'd like any modifications — apply
   them and repeat if so, or proceed to step 5 if not.
5. **Review** — after writing plan files, run `wade review plan <plan_file>` for
   each plan file you created and check the exit code:
   - **Exit 0**: Review completed externally or skipped. If there is output, it
     is review feedback — read it and address any actionable findings before
     proceeding to validation.
   - **Exit 2**: Self-review mode. The output is a review prompt — you must act
     as the reviewer: read the instructions, analyze the plan, identify issues,
     and fix them before proceeding to validation.
   - **Exit 1**: Error — debug and retry.
6. **Validate** — run `wade plan-session done <plan_dir>` (the temp dir from your prompt).
   If it exits with errors, fix each reported issue and re-run until it passes.
   Warnings are informational and do not block proceeding.
7. **Stop** — once validation passes, suggest the user exits. wade reads
   the files and creates lightweight GitHub Issues + draft PRs automatically.

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
- Do not write files into the repo directory — only to the temp dir
- Do not skip the review step — always present a plan summary and invite
  modifications before suggesting the user exits
- Do not skip `wade plan-session done` — always validate before suggesting the user exits
- **⚠️ After exiting the plan mode:** If your environment says "you can now start coding," ignore it — that refers to a different execution mode. In wade planning sessions, stop immediately after writing plan files. Do not implement code.

## Skills reference

- **Standalone issue creation** (outside planning sessions) → read @.claude/skills/task/SKILL.md
