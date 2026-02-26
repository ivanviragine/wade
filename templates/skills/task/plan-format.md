# Plan File Format

Reference for the `.md` format that `ghaiw task create --plan-file` expects.

## Structure

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

## Rules

| Element | Rule |
|---------|------|
| **Title** | First `# Heading` line becomes the GitHub issue title. Required. Max 256 chars (truncated with a warning if exceeded). |
| **Body** | Everything after the title heading becomes the issue body. Full markdown supported. |
| **Label** | Applied automatically from `.ghaiw.yml` config (`issue_label`). |
| **Complexity** | Optional `## Complexity` section with one value: `easy`, `medium`, `complex`, or `very_complex`. Used by `ghaiw work start` to auto-select the AI model. |
| **Plan Summary** | When issues are created through `ghaiw task plan`, ghaiw appends a managed `## Plan Summary` section with `### Usage` to the GitHub issue body after creation. If available from the AI CLI output, the summary includes session total/input/output/cached token counts, estimated premium requests, and a `### Model Breakdown` table for per-model usage. |
| **Sections** | Context, Proposed Solution, Tasks, and Acceptance Criteria are recommended but not enforced. |

## Complexity values

| Value | Typical use |
|-------|-------------|
| `easy` | Trivial fix, docs change, config tweak (<100 LOC) |
| `medium` | Small feature or bug fix (100-300 LOC) |
| `complex` | Multi-file feature or significant refactor (300-600 LOC) |
| `very_complex` | Large feature, cross-cutting concern, or architecture change (>600 LOC) |

`ghaiw work start` maps these to model names configured in `.ghaiw.yml`
(`model_easy`, `model_medium`, `model_complex`, `model_very_complex`).
If the complexity field is absent or no model is configured, the default
model for the AI tool is used.

## Title guidelines

- Keep it short, actionable, and specific
- Use imperative mood: "Add X", "Fix Y", "Refactor Z"
- Include the scope: "Add rate limiting to auth endpoints"
- Avoid vague titles: ~~"Improvements"~~, ~~"Update stuff"~~

## Task checklist guidelines

- Each task should map to a concrete code change
- Tasks should be ordered by implementation sequence
- Include test tasks explicitly: `- [ ] Add tests for X`
- Include documentation tasks when applicable: `- [ ] Update README`

## Multi-issue plans

When creating multiple issues from one plan, each issue gets its own `.md`
file. Use the naming convention:

```
plan-1-schema-changes.md
plan-2-api-endpoint.md
plan-3-ui-panel.md
```

Each file follows the same format above — fully self-contained with its own
title, context, tasks, and acceptance criteria.
