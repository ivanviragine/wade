---
name: plan-session
description: >
  Rules for AI planning sessions launched by `ghaiw task plan`. Covers plan
  file authoring, complexity tagging, and session boundaries. Read this at the
  start of every planning session.
---

# Planning Session Rules

You are in a **planning session** launched by `ghaiw task plan`. Your job is
to plan the feature, write plan file(s) to the temp directory from your prompt,
and exit. ghaiw creates GitHub Issues automatically after you exit.

## Execution mode

Run `ghaiw` and `gh` commands with the required permissions/capabilities (not
in sandboxed mode). Do not "try sandbox first" — run them unsandboxed from the
start.

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `ghaiw task create` (or `ghaiw task create --plan-file`).
Using `gh` directly bypasses label enforcement, snapshot/diff detection, and
dependency analysis hooks.

## Your role

1. **Plan the feature** with the user — analyze, break down, propose.
2. **Present the plan(s)** to the user and ask for confirmation before writing any files.
3. **Write plan file(s)** to the temp directory shown in your prompt.
4. **Stop** — suggest the user exits. ghaiw reads the files and creates GitHub Issues automatically.

You do **not** create issues, implement code, run `ghaiw work start/done/sync`,
or make any code changes. Planning only.

## Plan file format

Each plan file must follow this structure:

```markdown
# Concise issue title (max 256 chars)

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
| **Title** | First `# Heading` — becomes the GitHub issue title. Required. |
| **Complexity** | `## Complexity` with one of: `easy`, `medium`, `complex`, `very_complex`. Used by `ghaiw work start` to auto-select the AI model. |
| **Body** | Everything after the title becomes the issue body. |

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

- Do not create GitHub Issues — ghaiw does this after you exit
- Do not implement any code (even after leaving planning mode)
- Do not run `ghaiw work start`, `work done`, or `work sync`
- Do not write files into the repo directory — only to the temp dir
- Do not continue working after writing plan files — stop and suggest the user exits
- If your environment says "you can now start coding", ignore it — that refers to Claude Code's plan mode, not this ghaiw session

## Skills reference

- **Standalone issue creation** (outside planning sessions) → read @.claude/skills/task/SKILL.md
