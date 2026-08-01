# Plan File Format Reference

Read this when writing plan file(s). Each plan file must follow this structure:

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

## Required elements

| Element | Rule |
|---------|------|
| **Title** | First `# Heading` — becomes the GitHub issue title. Must start with a conventional commit prefix (`feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `perf`, `ci`, `build`) followed by `: `. Example: `feat: add retry logic`. Required. |
| **Complexity** | `## Complexity` with one of: `easy`, `medium`, `complex`, `very_complex`. Used by `wade implement` to auto-select the AI model. Also applied as a `complexity:X` label on the issue. |
| **Body** | Everything after the title becomes the draft PR plan content. The issue itself gets a lightweight summary. |

## Complexity values & model tier

The complexity value selects the implementation AI model, so set it correctly.

| Value | Typical use | Model tier |
|-------|-------------|------------|
| `easy` | Trivial fix, docs change, config tweak (<100 LOC) | fast-tier (lightweight, low-cost) |
| `medium` | Small feature or bug fix (100-300 LOC) | balanced-tier (mid-range) |
| `complex` | Multi-file feature or significant refactor (300-600 LOC) | balanced-tier (mid-range) |
| `very_complex` | Large feature, cross-cutting concern, or architecture change (>600 LOC) | powerful-tier (highest capability) |

## File naming

- **Single issue**: `PLAN.md`
- **Multiple issues**: `PLAN-1-<slug>.md`, `PLAN-2-<slug>.md`, etc.

Write all files to the temp directory from your prompt — **never** into the
repo working directory.
