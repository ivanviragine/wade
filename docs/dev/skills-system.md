# Skills, Snapshots, and Pointer System

This document covers project-skill discovery, compatibility installation, and
the `AGENTS.md` pointer. The workflow/binding model is documented in
[Workflows and Dynamic Skills](workflows-and-skills.md).

## Two worlds

Everything belongs either to the WADE source repository or to an inited target
project:

| WADE source | Per-session target output |
|---|---|
| `templates/workflows/<session>.md` | `.wade/session/WORKFLOW.md` |
| replaceable `templates/skills/<method>/` | `.wade/session/skills/builtin/<method>/` |
| a target project's native skill roots | `.wade/session/skills/project/<root>/<name>/` |
| compatibility/support skills in `templates/skills/` | `.claude/skills/<name>/` plus cross-tool projections |
| `templates/agents-pointer.md` | marker-delimited `## Git Workflow` block in target `AGENTS.md` |

`wade init` does not install any of these session outputs. It writes project
configuration and the init manifest. Interactive bootstrap creates the fixed
workflow and frozen session bundle; bounded foreign delegations create a
temporary `.wade/operations/<kind>/<invocation>/` bundle.

When developing WADE, edit only source templates. Never edit `.wade/session/`
or `.claude/skills/` in a bootstrapped worktree: they are generated artifacts.

## Three kinds of skill-related content

1. **Replaceable methodology skills** — `planning`, `implementation`,
   `review-comments`, `plan-review`, `code-review`, `batch-review`, and
   `dependency-analysis`. They own heuristics and rubrics only and must not
   mention WADE commands, `.wade/`, completion markers, review budgets, or
   session lifecycle.
2. **Compatibility phase pointers** — `plan-session`,
   `implementation-session`, and `review-pr-comments-session`. These deprecated
   tool-native skills only point old discovery paths to
   `.wade/session/WORKFLOW.md`; they are not the workflow source of truth.
3. **Fixed command-support skills** — currently `task`, `deps`, and `knowledge`.
   These may mention WADE because they are not replaceable session strategy
   slots. Session definitions declare which support skills are needed.

Do not register replaceable generic names in `SKILL_FILES`. A target project may
legitimately own `.claude/skills/implementation` or
`.agents/skills/code-review`; compatibility reconciliation must never overwrite
or prune those names.

## Compatibility installation

`skills/installer.py` projects only compatibility and support skills into the
worktree's canonical `.claude/skills/` root. Cross-tool skill roots are derived
from Crossby's `SKILLS_DIR` mapping and symlinked to that canonical root. The
deprecated `PLAN_SKILLS`, `IMPLEMENT_SKILLS`, `REVIEW_SKILLS`, and `DEPS_SKILLS`
exports remain for one compatibility window; runtime code derives selections
from `SessionDefinition.support_skills` through
`compatibility_skills_for_session()`.

Installation rules:

- target projects receive copies;
- WADE self-init worktrees receive symlinks to their live
  `templates/skills/` tree for authoring convenience;
- unknown user-owned directories are preserved;
- only names in `MANAGED_SKILL_NAMES` are reconciled or pruned;
- phase pointers have no injected partials or reference trees;
- native links are conveniences only and are never active session snapshots.

The same Crossby mapping drives compatibility projections and project discovery.
Contract tests pin the Crossby version/API behavior, root symlink behavior,
supported tool coverage, and filtered scene handling. Re-run those tests and
inspect tool coverage whenever the Crossby dependency pin changes.

## Project-skill discovery

`skills/discovery.py` scans supported skill roots in this order:

1. the session worktree;
2. the main checkout, when it differs.

A directory is a skill only when it contains `SKILL.md`. Discovery deduplicates
tool roots resolving to the same location and applies `skills.project.include`
and `exclude` patterns. The worktree wins for the same source-root/name identity;
main-only local or ignored skills are added afterward. This prevents a dirty
tracked main-checkout copy from replacing branch-specific worktree content while
still making local-only skills available to the session.

Crossby scene projections are selection views, not full inventories. If a
filtered projection is active alongside an unfiltered source, WADE warns and
scans the source. If only the projection is available, discovery fails rather
than silently omitting skills.

Resolution forms are explicit:

- `builtin:<name>` — a packaged replaceable default;
- `project:<name>` — a unique discovered project skill;
- `path:<repository-relative-skill-directory>` — an exact source when names
  collide.

Same-name/different-content `project:` candidates are ambiguous and fail.
Identical paths or content deduplicate. Missing, unsafe, ambiguous, and
oversized configured/active refs fail before AI launch or provider mutation.

WADE self-init is special in two narrow ways: discovery excludes tool-native
links resolving into packaged/live `templates/skills/`, while `builtin:` refs
resolve from the current worktree's live templates. Their physical session copy
still freezes those developer edits for the session.

## Validation and physical snapshots

Every discovered or selected skill is recursively inspected before copying:

- `SKILL.md` is required;
- only regular files and directories are accepted;
- broken, cyclic, or project-external symlinks are rejected;
- safe internal symlinks are dereferenced;
- file-count, per-file, and total-byte limits are enforced;
- relative paths and file contents feed a deterministic SHA-256 digest.

Interactive materialization writes a staging directory and atomically replaces
only `.wade/session/`. It copies every discovered project skill for optional
on-demand use, the active built-ins, fixed support instructions, workflow
references, `AVAILABLE_SKILLS.md`, and `manifest.json`. Availability does not
activate a skill: only ordered manifest bindings control WORK and REVIEW.

Session snapshots never point to the main checkout and never modify a native
project skill. Refresh replaces only `.wade/session/`; durable review history in
`.wade/reviews/` and any live `.wade/operations/` bundle remain outside that
transaction. Successful foreign operations remove their own bundle; failed
recoverable operations may retain it.

## Documentation targets

`src/wade/skills/doc_targets.py` detects the documentation files a project
actually maintains. `detect_doc_targets()` returns existing root files in this
order—`README.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`—plus `docs/` when
it is source rather than generated output. `format_doc_targets()` falls back to
the project's documentation generally when none exists.

The fixed implementation and PR-comment workflows render this list into
`templates/workflows/_partials/documentation-step.md`. The target detector no
longer expands a skill partial. After updating docs or deciding they are not
needed, the session records a current-commit receipt; `done` verifies it.

`docs/` is treated as generated when `docs/_build` exists or `.gitignore`
contains a bare `docs` entry. Site-generator configuration such as `mkdocs.yml`,
`docs/conf.py`, or `docs/.vitepress` identifies hand-authored source and is not
an exclusion signal.

## Context and progressive disclosure

The old combined phase-skill ceiling is replaced by separately testable
surfaces:

| Surface | Limit |
|---|---:|
| SessionStart reminder | 800 characters |
| interactive launch prompt | 2,000 characters |
| fixed rendered workflow | 12,000 characters |
| active WORK `SKILL.md` text | 6,000 characters |
| launch + workflow + WORK method | 20,000 characters |
| on-demand catalog excerpt | 4,000 characters |
| delegation method text | 8,000 characters |
| delegation envelope excluding operation input | 10,000 characters |

The pre-extraction default combined surfaces measured approximately 14.5k
characters. The 20k ceiling permits the deliberate workflow/method split; it is
an upper bound, not a size target. `tests/integration/test_skill_context_budget.py`
pins interactive surfaces. Delegation composition separately enforces its method
and envelope limits.

Workflow references under `.wade/session/reference/` hold lifecycle recovery and
output contracts. Skill-local `reference/`, `scripts/`, and assets travel with
the skill and remain methodology resources. The launch prompt loads the fixed
workflow and active WORK method; REVIEW text is loaded only inside the bounded
review operation. `AVAILABLE_SKILLS.md` is read on demand.

## AGENTS.md and CLAUDE.md pointer

`AGENTS.md` is canonical in this repo; the committed `CLAUDE.md` symlink exposes
the same content to Claude. In a target worktree, bootstrap prefers an existing
`AGENTS.md`, then `CLAUDE.md`, and otherwise creates `AGENTS.md`.

The injected pointer is deliberately small. It tells the agent to read
`.wade/session/WORKFLOW.md`, its selected methodology, and the task input. The
project's own instructions precede the pointer and remain authoritative for
project policy; the fixed WADE workflow remains authoritative for WADE lifecycle
mechanics.

The pointer is marker-delimited:

```markdown
<!-- wade:pointer:start -->
## Git Workflow
...
<!-- wade:pointer:end -->
```

`skills/pointer.py` detects, compares, refreshes, and removes this block. It also
migrates the old unmarked `## Git Workflow` form and deletes an otherwise empty
file during removal. Change injected text in `templates/agents-pointer.md`, not
in a generated worktree copy.

## Diagnostics

- `wade skills list` shows built-ins and the validated merged project inventory.
- `wade skills resolve --session <kind>` and `--delegation <kind>` show ordered
  refs, precedence candidates, winner, provenance, and binding digest.
- `wade skills check` validates discovery and every explicit configured ref.
- `wade session describe` displays the active frozen manifest.
- `wade session refresh-skills` explicitly rediscovers and atomically replaces
  the current session bundle.

`wade check-config` also validates configured refs. Diagnostics are read-only
except the explicitly named refresh command.
