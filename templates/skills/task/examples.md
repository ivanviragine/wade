# task — Examples

## Single-issue example (small scope)

**Plan summary:** Add a `/api/health` endpoint that returns app version and
uptime.

**Assessment:**
> This plan is ~100 LOC touching 3 files (router, schema, test), all related
> to a single health-check endpoint. I recommend keeping it as **1 issue**.

**Result:** one plan file (`PLAN.md`) → one issue.

```markdown
# feat: add health check endpoint

## Complexity
easy

## Context / Problem
There is no way to verify the app is running or check its version
programmatically.

## Proposed Solution
Add a `GET /api/health` endpoint returning `{ "status": "ok", "version": "..." }`.

## Tasks
- [ ] Create `HealthResponse` schema
- [ ] Add `/api/health` route
- [ ] Register router
- [ ] Add tests

## Acceptance Criteria
- [ ] `GET /api/health` returns 200 with version string
- [ ] Endpoint requires no authentication
- [ ] Tests pass
```

```bash
wade task create --title "feat: add health check endpoint" --body-file PLAN.md
# Created issue #42: feat: add health check endpoint
```

---

## Multi-issue example (large PRD)

**Plan summary:** Add user preferences — schema, API, UI panel, and
notification settings.

**Assessment:**
> This plan spans ~900 LOC across 4 distinct areas: database schema, API
> endpoints, UI components, and notification integration. I recommend
> splitting into **3 issues**:
> 1. Add user preferences schema and migration (~200 LOC, 4 files)
> 2. Add preferences API endpoints (~300 LOC, 5 files)
> 3. Add preferences UI panel (~400 LOC, 6 files)

**User adjustment:**
> User: "Merge the schema and API into one issue — they're tightly coupled."

**Revised breakdown:**
> 1. Add user preferences schema, migration, and API (~500 LOC, 9 files)
> 2. Add preferences UI panel (~400 LOC, 6 files)

**Result:** two plan files (`PLAN-1-schema-api.md`, `PLAN-2-ui-panel.md`) → two
issues + **one** parent. Because the UI depends on the API, the parent is the
tracking issue that `wade task deps` creates (not a separate epic). Each plan
file carries a conventional-commit `# Title` and a `## Complexity` value.

### Issue creation

```bash
wade task create --title "feat: add user preferences schema and API" \
  --body-file PLAN-1-schema-api.md
# Created issue #50: feat: add user preferences schema and API

wade task create --title "feat: add preferences UI panel" \
  --body-file PLAN-2-ui-panel.md
# Created issue #51: feat: add preferences UI panel
```

### Parent issue — dependency analysis

This set has a dependency (#51 → #50), so run `wade task deps` yourself after
creating the issues — **pass the issue numbers explicitly**, since installed
agents run in a non-TTY shell where bare `wade task deps` (no numbers) cannot
fall back to its interactive picker and exits with "Provide at least 2 issue
numbers.":

```bash
wade task deps 50 51
```

This updates each issue with cross-references **and** creates the tracking
issue that serves as the single parent — do **not** also create an epic. When a
project uses `wade plan` instead, wade runs the same dependency analysis
**automatically** after the planning session exits, producing the same tracking
issue:

```
Multiple issues created — running dependency analysis...

Found 1 dependency edge(s):
  #51 → #50 (API must exist before UI can call it)

Updating issue bodies with dependency refs...
  Updated #50
  Updated #51

Creating tracking issue with execution plan...
  Created tracking issue #52
  https://github.com/user/repo/issues/52
```

The **tracking issue** is the parent and contains the full execution plan:
- Topologically sorted tasklist (checkbox format)
- Mermaid dependency graph

Individual issues get lightweight cross-references ("Depends on" / "Blocks") only.

**Final report:**
```
Created 2 issues + 1 tracking issue:
  #50 — feat: add user preferences schema and API (~500 LOC)
  #51 — feat: add preferences UI panel (~400 LOC)
  #52 — Tracking: #50, #51 (execution plan + dependency graph)
```

You can also run dependency analysis manually — always pass issue numbers
explicitly (bare `wade task deps` only offers interactive selection, and that
requires a TTY a running agent doesn't have):

```bash
wade task deps 50 51               # analyze specific issues
wade task deps 50 51 --ai claude   # override AI tool
```

### Independent issues → epic instead

For a set of **independent** issues (no dependencies, so no `wade task deps`
run), create a manual **epic** as the single parent — never create both a
tracking issue and an epic. Write the epic body to a file and create it
non-interactively:

```markdown
# feat(epic): user preferences feature

## Overview
Add the ability for users to save and manage display and notification preferences.

## Sub-issues
- [ ] #50 — feat: add user preferences schema and API
- [ ] #51 — feat: add preferences UI panel
```

```bash
wade task create --title "feat(epic): user preferences feature" --body-file EPIC.md
# Created issue #52: feat(epic): user preferences feature
```
