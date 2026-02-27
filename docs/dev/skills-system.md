# Skills System

Detailed documentation for the skills, pointer, and installation system. For the compact overview of the "two worlds" boundary, see `AGENTS.md`.

## Two Worlds: ghaiw repo vs inited projects

This boundary is critical. Everything in this repo exists in one of two worlds:

| ghaiw repo (source) | Inited project (output) |
|---------------------|------------------------|
| `src/ghaiw/` | installed `ghaiw` binary (via pip/uv) |
| `templates/skills/<name>/SKILL.md` | `.claude/skills/<name>/SKILL.md` |
| `templates/agents-pointer.md` | `## Git Workflow` block in target `AGENTS.md` |
| `AGENTS.md` (this file) | target project's own `AGENTS.md` (different content) |
| `.ghaiw.yml` (this repo's config) | target project's own `.ghaiw.yml` |

When developing ghaiw, **only touch the left column**. The right column is what users get after running `ghaiw init` in their own projects.

## AGENTS.md and CLAUDE.md

`AGENTS.md` is the canonical agent guidance file for this repo. `CLAUDE.md` is a committed symlink -> `AGENTS.md`, providing Claude Code discovery without duplicating content. **Always edit `AGENTS.md` directly** — changes reflect in `CLAUDE.md` automatically via the symlink.

In inited projects, `ghaiw init` writes the workflow pointer to whichever of `AGENTS.md` / `CLAUDE.md` already exists (preferring `AGENTS.md`), or creates `AGENTS.md` if neither exists.

## Skill File Symlink Structure

When `ghaiw init` runs on this repo (self-init mode), the installer creates symlinks so edits to skill templates are reflected immediately:

```
.claude/skills/plan-session  ->  ../../templates/skills/plan-session  (symlink)
.claude/skills/work-session  ->  ../../templates/skills/work-session  (symlink)
.claude/skills/task          ->  ../../templates/skills/task          (symlink)
.claude/skills/deps          ->  ../../templates/skills/deps          (symlink)

.github/skills/              ->  (same targets, separate symlinks)
.agents/skills/              ->  (same targets, separate symlinks)
.gemini/skills/              ->  (same targets, separate symlinks)
```

Note: These symlinks are created by `ghaiw init` with `is_self_init=True`. If they don't exist, run `ghaiw init` in this repo to create them.

**Always edit `templates/skills/<name>/SKILL.md`** — never edit files inside `.claude/skills/`, `.github/skills/`, `.agents/skills/`, or `.gemini/skills/` directly. In this repo those are symlinks back to templates; in inited projects they are copies that would be overwritten by `ghaiw update`.

In inited projects (normal init), `ghaiw init` copies skill files (not symlinks), so agents in those projects read standalone files that don't change unless `ghaiw update` is run.

## Skill Installation Lifecycle

`ghaiw init` installs skills file-by-file via the `skills/installer.py` module. When adding a new skill:

1. Create the skill template in `templates/skills/<name>/SKILL.md`
2. Register the skill in `skills/installer.py` — add it to `SKILL_FILES` and optionally `ALWAYS_OVERWRITE`
3. Add the skill directory to the cleanup logic in `init_service.py` (deinit path)
4. Reference the skill from `plan-session/SKILL.md` or `work-session/SKILL.md` as appropriate

The self-init path creates symlinks from `.claude/skills/<name>` -> `../../templates/skills/<name>` to avoid file duplication when working on ghaiw itself.

## Agent Skills (templates/skills/)

> **Scope: inited projects.** The skill templates in `templates/skills/` are installed into inited projects by `ghaiw init`. They are *not* guidance for developing ghaiw itself — they teach AI agents in target projects how to use the ghaiw workflow. When you are developing ghaiw, treat these files as **output artifacts** you are authoring, not as rules you follow.

Skill templates are Markdown files installed to an inited project's `.claude/skills/` by `ghaiw init`, with symlinks from `.github/skills/`, `.agents/skills/`, and `.gemini/skills/` for cross-tool discovery. They teach AI agents the ghaiw workflow via phase-specific session skills and on-demand task skills.

### Phase-Specific Skill Architecture (for inited projects)

> **Scope: inited projects.** The following describes how ghaiw structures agent-facing documentation *in the projects it is installed into* — not how this repo's own documentation is organized.

Skills are organized into **phase skills** (one per session type) and **task skills** (on-demand reference):

1. **AGENTS.md pointer** — `ghaiw init` reads `templates/agents-pointer.md` and inserts its content into the target project's `AGENTS.md`. It directs agents to read the skill referenced in their clipboard prompt. **To change what gets injected into inited projects, edit `templates/agents-pointer.md`** — not this repo's own `## Git Workflow` section, which is only the self-installed copy for this repo.
2. **`templates/skills/plan-session/SKILL.md`** — Self-contained rules for planning sessions (`ghaiw task plan`). Covers plan file format, complexity tagging, session boundaries. No implementation rules.
3. **`templates/skills/work-session/SKILL.md`** — Self-contained rules for implementation sessions (`ghaiw work start`). Covers worktree safety, commit conventions, syncing, PR summaries, and session closing. No planning rules.
4. **`templates/skills/task/SKILL.md`** — On-demand skill for standalone issue creation outside of planning sessions.
5. **`templates/skills/deps/SKILL.md`** — On-demand skill for dependency analysis.

Each phase skill is self-contained — agents only read the skill for their current phase. This eliminates noise from irrelevant rules. When adding new agent-facing commands or workflows:
- **Put implementation rules** in `work-session/SKILL.md`
- **Put planning rules** in `plan-session/SKILL.md`
- **Create or update a task skill** in `templates/skills/` for on-demand reference
- Keep the AGENTS.md pointer minimal — it should only direct agents to their phase skill

### Pointer Placement & Precedence

The workflow pointer in `AGENTS.md` is strictly secondary to the project's own documentation.
- **Placement**: Append to the end of `AGENTS.md` (or intelligent insertion after existing sections), but **never** force it to the top. The project's own context and rules must come first.
- **Style**: Avoid overly aggressive alerts (e.g., `[!IMPORTANT]`) in the pointer itself, as it can distract from project-specific high-priority rules.
- **Precedence**: If a project's `AGENTS.md` defines rules that conflict with the workflow skill, the project's rules win. The workflow skill handles the *mechanics* of ghaiw; the project handles the *policy*.

## Pointer Marker System

The AGENTS.md workflow pointer uses HTML comment markers to enable robust detection and refresh:

```
<!-- ghaiw:pointer:start -->
## Git Workflow
...
<!-- ghaiw:pointer:end -->
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
