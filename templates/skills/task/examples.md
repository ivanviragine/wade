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
wade task create
# Created issue #42: feat: add health check endpoint
```

---

## Multi-issue example with epic (large PRD)

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
issues + one epic. Each plan file carries a conventional-commit `# Title` and a
`## Complexity` value.

### Issue creation

```bash
wade task create
# Created issue #50: feat: add user preferences schema and API

wade task create
# Created issue #51: feat: add preferences UI panel
```

### Epic issue (offered after sub-issues are created)

```markdown
# feat(epic): user preferences feature

## Overview
Add the ability for users to save and manage display and notification preferences.

## Sub-issues
- [ ] #50 — feat: add user preferences schema and API
- [ ] #51 — feat: add preferences UI panel
```

```bash
wade task create
# Created issue #52: feat(epic): user preferences feature
```

**Final report:**
```
Created 3 issues:
  #50 — feat: add user preferences schema and API (~500 LOC)
  #51 — feat: add preferences UI panel (~400 LOC)
  #52 — feat(epic): user preferences feature (links #50, #51)
```

### Dependency analysis (multi-issue)

For a standalone multi-issue set, run `wade task deps` yourself after creating
the issues. When a project uses `wade plan` instead, wade creates the issues and
draft PRs and runs `wade task deps` **automatically** after the planning session
exits — updating each issue and creating a tracking issue:

```
Multiple issues created — running dependency analysis...

Found 1 dependency edge(s):
  #51 → #50 (API must exist before UI can call it)

Updating issue bodies with dependency refs...
  Updated #50
  Updated #51

Creating tracking issue with execution plan...
  Created tracking issue #53
  https://github.com/user/repo/issues/53
```

The **tracking issue** contains the full execution plan:
- Topologically sorted tasklist (checkbox format)
- Mermaid dependency graph

Individual issues get lightweight cross-references ("Depends on" / "Blocks") only.

You can also run dependency analysis manually:

```bash
wade task deps               # select issues interactively
wade task deps --ai claude   # override AI tool
```
