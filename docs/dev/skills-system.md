# Skills System

Detailed documentation for the skills, pointer, and installation system. For the compact overview of the "two worlds" boundary, see `AGENTS.md`.

## Two Worlds: WADE repo vs inited projects

This boundary is critical. Everything in this repo exists in one of two worlds:

| WADE repo (source) | Inited project (output) |
|---------------------|------------------------|
| `src/wade/` | installed `wade` binary (via pip/uv) |
| `templates/skills/<name>/SKILL.md` | `.claude/skills/<name>/SKILL.md` |
| `templates/agents-pointer.md` | `## Git Workflow` block in target `AGENTS.md` |
| `AGENTS.md` (this file) | target project's own `AGENTS.md` (different content) |
| `.wade.yml` (this repo's config) | target project's own `.wade.yml` |

When developing wade, **only touch the left column**. The right column is what a project gets after adopting wade — `wade init` writes `.wade.yml` and the manifest, then worktree bootstrap (per `wade implement`/`plan`/`review` session, or a standalone `wade task deps`) installs the skills, pointer, and allowlists.

## AGENTS.md and CLAUDE.md

`AGENTS.md` is the canonical agent guidance file for this repo. `CLAUDE.md` is a committed symlink -> `AGENTS.md`, providing Claude Code discovery without duplicating content. **Always edit `AGENTS.md` directly** — changes reflect in `CLAUDE.md` automatically via the symlink.

In inited projects, **worktree bootstrap** (not `wade init`) writes the workflow pointer per session to whichever of `AGENTS.md` / `CLAUDE.md` already exists (preferring `AGENTS.md`), or creates `AGENTS.md` if neither exists.

## Skill File Symlink Structure

When a session bootstraps a worktree in this repo (self-init mode), the installer creates symlinks *in the worktree* so edits to skill templates are reflected immediately:

```
.claude/skills/plan-session              ->  processed copy (self-init exception via INJECT_SKILLS)
.claude/skills/implementation-session   ->  processed copy (self-init exception via INJECT_SKILLS)
.claude/skills/review-pr-comments-session ->  processed copy (self-init exception via INJECT_SKILLS)
.claude/skills/task                     ->  ../../templates/skills/task                     (symlink)
.claude/skills/deps                     ->  ../../templates/skills/deps                     (symlink)

.github/skills/              ->  (same targets, separate symlinks)
.agents/skills/              ->  (same targets, separate symlinks)
.cursor/skills/              ->  (same targets, separate symlinks)
```

Note: These symlinks are created **per session by worktree bootstrap** (`install_skills(..., is_self_init=True)`), not by `wade init`. They live in the session's worktree, not on main; a fresh session recreates them.

**Always edit `templates/skills/<name>/SKILL.md`** — never edit files inside `.claude/skills/`, `.github/skills/`, `.agents/skills/`, or `.cursor/skills/` directly. In this repo those are symlinks back to templates; in inited projects they are copies re-installed each session.

In inited projects, worktree bootstrap copies skill files (not symlinks) into each session's worktree, so agents read standalone files. They are refreshed every session, and skills no longer live on main at all (`wade update` actively migrates any legacy on-main skills off).

## Skill Installation Lifecycle

**Worktree bootstrap** installs skills file-by-file via the `skills/installer.py` module — per session, into the session's worktree. (`install_skills()` is called only from `bootstrap_worktree` in `implementation_service/bootstrap.py`, never from `wade init`.) When adding a new skill:

1. Create the skill template in `templates/skills/<name>/SKILL.md`
2. Register the skill in `skills/installer.py` — add it to `SKILL_FILES` and optionally `ALWAYS_OVERWRITE`. `SKILL_FILES` lists **every** file to install for a skill, including any `reference/<file>.md` (see [Progressive Disclosure](#progressive-disclosure-reference-files-and-the-context-budget)). Files not listed here are not installed and are not gitignored — `get_worktree_gitignore_entries()` derives its paths from this same map.
3. Add the skill directory to the cleanup logic in `init_service.py` (deinit path)
4. Reference the skill from `plan-session/SKILL.md`, `implementation-session/SKILL.md`, or `review-pr-comments-session/SKILL.md` as appropriate

The self-init path creates symlinks from `.claude/skills/<name>` -> `../../templates/skills/<name>` to avoid file duplication when working on wade itself. Exception: skills in `INJECT_SKILLS` (currently all three session skills) are always installed as processed copies — even in self-init mode — because their templates contain placeholder strings that must be expanded before agents read them (see Partial Templates below).

## Partial Templates

Shared content that appears verbatim in multiple skill templates lives in `templates/skills/_partials/`. Template files reference partials via placeholder strings defined in `_SKILL_PARTIALS` in `installer.py`:

```text
{user_interaction_prompt}  →  _partials/user-interaction.md
```

The installer expands these placeholders when copying skill files to a project. The `_partials/` directory is never installed into target projects — it is consumed at install time only.

**To add a new partial:**
1. Create `templates/skills/_partials/<name>.md`
2. Add an entry to `_SKILL_PARTIALS` in `installer.py`
3. Use the placeholder string in the relevant skill template(s)
4. Add the skill to `INJECT_SKILLS` if it is not already there

Partials carry **no H2 heading of their own** when a session folds multiple
sections around them. For example `user-interaction.md` is heading-less prose
injected inside each skill's `## Talking to the user` section — the skill owns the
single heading, so the fold never produces a duplicate H2.

Partials also carry **no step number**. `doc-update-step.md` is inserted at a
different position in each session (Step 2 in implement, after review; Step 1 in
review-pr-comments), so the numbered heading lives in each `SKILL.md` and the
partial holds only the shared body.

## Documentation Targets

The closing documentation pass names the files a project actually maintains.
That list is **detected per project**, not hardcoded: `install_skills` calls
`src/wade/skills/doc_targets.py` and injects the result as `{doc_targets}` into
`doc-update-step.md`.

- `detect_doc_targets(project_root)` returns the root doc files that exist
  (`README.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, in that order) plus
  `docs/` when it exists and is not generated build output.
- `format_doc_targets(targets)` renders a backtick-quoted list, falling back to
  *"the project's documentation, if it has any"* when nothing is detected, so the
  step still reads correctly in a doc-less project.

**Generated-docs guard.** Instructing an agent to edit generated output is worse
than silence — the edit is real, plausible, committed, and erased by the next
build. `docs/` is skipped when `docs/_build` exists or `.gitignore` contains a
bare `docs` entry. Site-generator *config* files (`mkdocs.yml`,
`docusaurus.config.js`, `docs/conf.py`, `docs/.vitepress`, `docs/book.toml`,
`docs/_config.yml`) are deliberately **not** treated as generated markers: under
each tool's default convention they mark `docs/` as hand-authored *source*, so
treating them as output would exclude `docs/` for the most common case.

`{doc_targets}` is a **computed** placeholder — it is resolved in
`install_skills`, not read from a file in `_SKILL_PARTIALS`. Two consequences:
`_expand_partials` re-applies `extra_partials` after the file-partial loop so a
placeholder nested inside an expanded partial still resolves, and the budget test
must render it explicitly (see below). Caller-supplied `extra_partials` win over
the computed value.

## Progressive Disclosure: reference/ files and the context budget

Every wade session opens with a launch prompt that inlines the phase `SKILL.md`
(via the bare `@` reference), before the agent reads any code. To keep that
opening payload small, each rule lives on exactly **one** surface, and reference
material is loaded **just-in-time** rather than eagerly.

### Ownership model

| Surface | Owns | Never contains |
|---|---|---|
| **`SKILL.md`** | the durable *how* + judgment a gate can't check + **one** copy of the workflow | recovery procedures, exit-code tables, format templates, restated checklists |
| **Launch prompt** (`templates/prompts/*.md`) | the *what* — this task/issue/plan-dir + a one-line "build a todo from the skill" + the first command | the workflow (the skill has it) |
| **`reference/*.md`** next to `SKILL.md` | recovery procedures, formats, edge cases — read on demand | anything on the happy path |
| **CLI output** | what to do *now*, at the moment something fails (already emitted by wade) | — |

Each phase skill points at its reference files with a one-line `@`-pointer.
`implementation-session/SKILL.md` points at `reference/recovery.md` for
sync/catchup conflict handling, `reference/pr-summary-format.md` for the
PR-summary format, `reference/doc-update.md` for the closing documentation pass,
and — from the `## Skills reference` index — `reference/tracking-issues.md`
(child/epic issues) and `reference/new-plan.md` (finalizing a plan mid-session).
`review-pr-comments-session` points at its own `reference/recovery.md` and
`reference/doc-update.md`. The `task` skill uses the same pattern with
`plan-format.md` + `examples.md`.

Reference files must be registered in `SKILL_FILES` (see [Skill Installation
Lifecycle](#skill-installation-lifecycle)) using the `reference/<file>.md` path,
or they are not installed. Review needs its **own** `reference/recovery.md` and
`reference/doc-update.md` — it cannot point at implementation-session's, because
`REVIEW_SKILLS` installs only `review-pr-comments-session`, `task`, and
`knowledge`. Some duplication between the paired files is expected and correct.

### The ≤ 8,000-char budget test

`tests/integration/test_skill_context_budget.py` pins the combined size of the
session-start payload — launch prompt + rendered `SKILL.md` (partials expanded,
reviews enabled) — at **≤ 8,000 chars** for each of implement / plan / review, so
the budget cannot silently regress. The unit is **chars** (a deliberate proxy for
tokens; measured token savings differ slightly). If a skill edit pushes a session
over budget, move the added detail into a `reference/*.md` and leave a one-line
pointer rather than inflating the always-loaded `SKILL.md`.

**Computed placeholders must be rendered, not left literal.** `{doc_targets}` is
resolved per project at install time (see [Documentation
Targets](#documentation-targets)) rather than read from a partial file, so
`_expand_partials` alone leaves the 14-char placeholder in place and
under-measures every real install. The test therefore expands it with the
largest set the detector can produce (all root doc files + `docs/`, ~64 chars) so
the ceiling reflects a worst-case project. Any future computed placeholder added
to a `SKILL.md` must be given the same treatment, or the budget silently drifts.

## Agent Skills (templates/skills/)

> **Scope: inited projects.** The skill templates in `templates/skills/` are installed into inited projects **per session by worktree bootstrap**. They are *not* guidance for developing wade itself — they teach AI agents in target projects how to use the wade workflow. When you are developing wade, treat these files as **output artifacts** you are authoring, not as rules you follow.

Skill templates are Markdown files installed to an inited project's `.claude/skills/` **per session by worktree bootstrap** (`bootstrap_worktree`), with symlinks from `.github/skills/`, `.agents/skills/`, and `.cursor/skills/` for cross-tool discovery. They teach AI agents the wade workflow via phase-specific session skills and on-demand task skills.

### Phase-Specific Skill Architecture (for inited projects)

> **Scope: inited projects.** The following describes how wade structures agent-facing documentation *in the projects it is installed into* — not how this repo's own documentation is organized.

Skills are organized into **phase skills** (one per session type) and **task skills** (on-demand reference):

1. **AGENTS.md pointer** — **worktree bootstrap** (via `pointer.ensure_pointer`) reads `templates/agents-pointer.md` and inserts its content into the target project's `AGENTS.md` per session. It directs agents to read the skill referenced in their clipboard prompt. **To change what gets injected into inited projects, edit `templates/agents-pointer.md`** — not this repo's own `## Git Workflow` section, which is only the self-installed copy for this repo.
2. **`templates/skills/plan-session/SKILL.md`** — Self-contained rules for planning sessions (`wade plan`). Covers plan file format, complexity tagging, session boundaries. No implementation rules.
3. **`templates/skills/implementation-session/SKILL.md`** — Self-contained rules for implementation sessions (`wade implement`). Covers worktree safety, commit conventions, syncing, PR summaries, and session closing. No planning rules.
4. **`templates/skills/review-pr-comments-session/SKILL.md`** — Self-contained rules for review sessions (`wade review pr-comments`). Covers review fetching, thread resolution, syncing, and session closing. No planning or implementation rules.
5. **`templates/skills/task/SKILL.md`** — On-demand skill for standalone issue creation outside of planning sessions.
6. **`templates/skills/deps/SKILL.md`** — On-demand skill for dependency analysis.

Each phase skill is self-contained — agents only read the skill for their current phase. This eliminates noise from irrelevant rules. When adding new agent-facing commands or workflows:
- **Put implementation rules** in `implementation-session/SKILL.md`
- **Put planning rules** in `plan-session/SKILL.md`
- **Put review rules** in `review-pr-comments-session/SKILL.md`
- **Create or update a task skill** in `templates/skills/` for on-demand reference
- Keep the AGENTS.md pointer minimal — it should only direct agents to their phase skill

### Pointer Placement & Precedence

The workflow pointer in `AGENTS.md` is strictly secondary to the project's own documentation.
- **Placement**: Append to the end of `AGENTS.md` (or intelligent insertion after existing sections), but **never** force it to the top. The project's own context and rules must come first.
- **Style**: Avoid overly aggressive alerts (e.g., `[!IMPORTANT]`) in the pointer itself, as it can distract from project-specific high-priority rules.
- **Precedence**: If a project's `AGENTS.md` defines rules that conflict with the workflow skill, the project's rules win. The workflow skill handles the *mechanics* of wade; the project handles the *policy*.

## Pointer Marker System

The AGENTS.md workflow pointer uses HTML comment markers to enable robust detection and refresh:

```
<!-- wade:pointer:start -->
## Git Workflow
...
<!-- wade:pointer:end -->
```

**Functions in `skills/pointer.py`:**
- `has_pointer(file_path)` — Checks if file has marker-delimited pointer block
- `extract_pointer_content(file_path)` — Extracts text between markers (for staleness comparison)
- `remove_pointer(file_path)` — Removes marker-wrapped block; falls back to old-style `## Git Workflow` section removal for backward compatibility
- `write_pointer(file_path)` — Appends marker-wrapped pointer to file
- `ensure_pointer(project_root)` — High-level: find AGENTS.md/CLAUDE.md, detect staleness, refresh if needed

**`ensure_pointer()` logic:**
- Markers present -> Extract inner content and compare to current template
  - Match -> No-op (already current)
  - Different -> Remove old block and write new one (refresh)
- Old-style (no markers) -> Remove via line-based fallback and write with markers (migrate)
- Not present -> Create new file or append (new install)

**`remove_pointer()` removal:**
- Uses marker-based detection (primary), old-style `## Git Workflow` section scanning (fallback)
- Deletes file if it would be empty after removal
- Gracefully preserves project content in AGENTS.md
