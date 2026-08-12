# Plan File Format Reference

Read this when writing plan file(s). Each plan file must follow this structure:

```markdown
# type: concise issue title (max 256 chars)

## Complexity
medium

## Base Branch
develop

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
| **Title** | First `# Heading` — becomes the GitHub issue title. Must start with a conventional commit prefix (`feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `perf`, `ci`, `build`) followed by `:` and a space. Example: `feat: add retry logic`. Required. |
| **Complexity** | `## Complexity` with one of: `easy`, `medium`, `complex`, `very_complex`. Used by `wade implement` to auto-select the AI model. Also applied as a `complexity:X` label on the issue. |
| **Base Branch** | *Optional.* `## Base Branch` with a single branch name. When present, the draft PR branches from and targets that base; the worktree is cut from it and the work merges back into it. **Omit** to use the project's configured main branch (the default). The value must be a well-formed git branch name that exists — or will exist — before implementation. |
| **Body** | Everything after the title becomes the draft PR plan content. The issue itself gets a lightweight summary. |

## Base branch (optional)

Only add a `## Base Branch` section when the user explicitly wants the work to
target a branch other than the project's main branch:

```markdown
## Base Branch
develop
```

- Omit the section entirely for the common case — everything defaults to the
  configured main branch and behavior is unchanged.
- The value is a single branch name (no spaces or special characters). A
  malformed value fails `wade plan-session done`.
- A present `## Base Branch` heading **must** name a branch — an empty section
  (heading with no value) is rejected by `wade plan-session done`, not treated
  as "omitted". Remove the heading to default to main.
- The branch must exist (locally or on the remote) before you run
  `wade implement`, or draft-PR creation fails with an actionable error.
- To override or set a base at implement time instead, use
  `wade implement <N> --base <branch>` — it retargets the draft PR too.

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
