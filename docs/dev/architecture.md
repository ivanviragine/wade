# Architecture Reference

Detailed architecture documentation for WADE development. For the compact overview, see `AGENTS.md`.

## Commands Reference

```bash
# Install (development)
uv pip install -e ".[dev]"

# Run tests
./scripts/test.sh                                                         # all tests (excludes live)
./scripts/test.sh tests/unit/                                             # unit tests only
./scripts/test.sh tests/integration/                                      # integration tests only
./scripts/test-e2e.sh                                                     # deterministic e2e contract lane
./scripts/test-e2e-docker.sh                                              # deterministic e2e in Docker (CI-equivalent)
RUN_LIVE_GH_TESTS=1 WADE_LIVE_REPO=/path/to/repo ./scripts/test-live-gh.sh # manual live GitHub lane
RUN_LIVE_AI_TESTS=1 ANTHROPIC_API_KEY=... WADE_LIVE_AI_TIMEOUT=45 ./scripts/test-live-ai.sh # manual live AI lane (API-key-backed, not /login session auth)

# Run a single test file
./scripts/test.sh tests/unit/test_config/test_loader.py

# Run tests matching a pattern
./scripts/test.sh -k "test_pattern"

# Type check + lint (both)
./scripts/check.sh
./scripts/check.sh --types    # mypy only
./scripts/check.sh --lint     # ruff only

# Auto-format source
./scripts/fmt.sh

# Version bump
uv run python scripts/auto_version.py patch           # bug fixes, docs (0.1.0 -> 0.1.1)
uv run python scripts/auto_version.py minor           # new features, flags (0.1.0 -> 0.2.0)
uv run python scripts/auto_version.py major           # breaking changes (0.1.0 -> 1.0.0)
uv run python scripts/auto_version.py minor --dry-run # preview only

# Generate changelog
uv run python scripts/changelog.py                   # write CHANGELOG.md
uv run python scripts/changelog.py --stdout          # print to stdout
uv run python scripts/changelog.py --tag v1.0.0      # label unreleased as v1.0.0

```

## Package Structure

```
src/wade/
├── __init__.py          # __version__
├── __main__.py          # python -m wade
├── cli/                 # Typer commands (thin dispatch)
│   ├── main.py          # Root app + interactive menu, subcommand registration
│   ├── admin.py         # init, update, deinit, check-config, shell-init
│   ├── task.py          # task create/list/read/update/close/deps
│   ├── worktree.py      # worktree list/remove/cd (interactive menu)
│   ├── implementation_session.py  # implementation-session check/sync/done
│   ├── review_pr_comments_session.py # review-pr-comments-session check/sync/done/fetch/resolve
│   ├── review.py        # review plan/implementation/pr-comments/batch
│   ├── plan_session.py  # plan-session done
│   ├── hook.py          # `wade hook` — write-guard entry point for AI tool hooks
│   └── autocomplete.py  # Shell autocompletion helpers
├── models/              # Pydantic domain models (pure data, no I/O)
│   ├── config.py        # ProjectConfig, ProjectSettings, AIConfig, ComplexityModelMapping
│   ├── task.py          # Task, PlanFile, Complexity, Label, TaskState
│   ├── session.py       # ImplementationSession, WorktreeState, SyncResult, SyncEvent, MergeStrategy
│   ├── delegation.py    # DelegationRequest, DelegationResult, DelegationMode
│   ├── permission.py    # PermissionMode (autonomy axis), launch-kwargs helpers
│   ├── review.py        # PRReviewStatus, ReviewThread, ReviewComment
│   ├── batch.py         # BatchIssueContext, BatchReviewContext
│   ├── deps.py          # DependencyEdge, DependencyGraph
│   └── events.py        # Typed event models
├── services/            # Business logic (orchestration)
│   ├── task_service.py  # Task CRUD, plan parsing, labels
│   ├── implementation_service.py  # Implementation session lifecycle
│   ├── plan_service.py  # AI planning sessions
│   ├── review_service.py           # PR review session lifecycle
│   ├── review_delegation_service.py # AI-powered review delegation
│   ├── batch_review_service.py      # Batch issue review
│   ├── deps_service.py  # Dependency analysis
│   ├── smart_start.py   # Smart routing (wade <N> → implement or review)
│   ├── init_service.py  # Project initialization
│   ├── check_service.py # Safety checks, config validation
│   ├── ai_resolution.py # AI tool/model/effort resolution logic
│   ├── delegation_service.py # Delegation mode resolution
│   └── prompt_delivery.py   # Initial message delivery for AI tools
├── providers/           # Task backend providers (ABC + registry)
│   ├── base.py          # AbstractTaskProvider
│   ├── github.py        # GitHubProvider (gh CLI subprocess)
│   ├── clickup.py       # ClickUpProvider (REST API)
│   └── registry.py      # Provider registry (register_provider / get_provider)
├── git/                 # Git operations (all subprocess)
│   ├── repo.py          # Repo introspection
│   ├── worktree.py      # Worktree create/remove/list
│   ├── branch.py        # Branch naming, creation, deletion
│   ├── sync.py          # Fetch + merge, conflict detection
│   └── pr.py            # PR creation, merge
├── hooks/               # Guard policies (invoked via `wade hook` / `wade-hook`)
│   ├── cli.py           # Lean `wade-hook` entry point (dialect maps, guard routing)
│   └── policies.py      # worktree_containment / plan_artifact_only / shell_containment / session_complete
├── skills/              # Skill file management
│   ├── installer.py     # Install/update/remove skill files
│   └── pointer.py       # AGENTS.md pointer insertion/detection
├── config/              # Configuration management
│   ├── loader.py        # Find + parse .wade.yml (walk up from CWD)
│   ├── schema.py        # Re-exports from models (Pydantic Settings)
│   └── migrations.py    # Config migration pipeline (ensure version key)
├── ui/                  # Terminal UI (Rich)
│   ├── console.py       # Console class
│   └── prompts.py       # confirm, input, select, menu
├── logging/             # Structured logging
│   ├── setup.py         # structlog configuration
│   └── context.py       # Session context binding
└── utils/               # Shared utilities
    ├── clipboard.py     # Cross-platform clipboard
    ├── terminal.py      # Tab title, TTY detection, launch_in_new_terminal
    ├── slug.py          # Title -> URL-safe slug
    ├── markdown.py      # Plan file parsing
    ├── plan_validation.py # Lean plan-file validator (discover/validate/has_valid_plan) — Stop-path safe
    ├── process.py       # Subprocess helpers
    ├── http.py          # HTTPClient for REST API providers
    ├── markers.py       # sha-keyed .wade/<name>@<sha> completion markers (done, reviewed, stop-nudged)
    ├── update_check.py  # Version checking, self-upgrade hints
    └── install.py       # Self-upgrade helpers (venv/source detection, re-exec)
```

> `templates/hooks/pre-push` is the completion-gate backstop script installed
> per-worktree at `.wade/githooks/pre-push` (see *Completion Gates & the
> `done`-marker* below).
>
> **The `db/` package is unused scaffolding** — deliberately omitted from the
> tree above. No code path under `services/` or `cli/` writes
> session/worktree/PR rows, so its sole reader
> (`implementation_service/cleanup._preserve_session_data`, a single
> `SessionRepository.get_by_worktree_path` call) always gets an empty result and
> falls back to directory-presence detection. Real persisted state lives in
> GitHub (PR/issue body markers, labels) and worktree files, not SQLite. The
> `db/` code still exists but is inert; removal is tracked as **#357 C5**.

## AI Tool Layer (external: crossby)

AI tool adapters, model/effort resolution primitives, the model registry, and per-tool config (allowlists, hooks, defaults) are **not** part of this repo. They live in the external [`crossby`](https://github.com/ivanviragine/crossby) package. The exact version range is pinned in `pyproject.toml` (the `crossby` dependency entry) — that pin is the single source of truth; this doc intentionally does not restate the range so the two cannot drift. This replaced wade's formerly-internal `ai_tools/` and parts of `config/` and `data/` (see `feat: replace internal AI tool layer with the crossby dependency (#215)`).

| What | Lives in crossby | Used from wade via |
|------|-------------------|---------------------|
| `AbstractAITool` + adapters (Claude, Cursor, Copilot, Codex, OpenCode, Antigravity IDE = GUI launcher for the `antigravity` desktop app, Antigravity CLI = `agy` terminal agent, VS Code) | `crossby.ai_tools` | `services/ai_resolution.py`, `delegation_service.py`, `review_service.py`, `plan_service.py`, `prompt_delivery.py` |
| `AIToolID`, `AIModel`, `ModelTier`, `TokenUsage`, `AIToolCapabilities`, `EffortLevel` | `crossby.models.ai` | re-exported from `wade.models` |
| Model registry (probed from CLIs, was `data/models.json`) | `crossby.data.get_models_for_tool()` | `ai_resolution.py`, `init_service.py` |
| Per-tool default model tiers (was `config/defaults.py`) | `crossby.config.defaults.get_defaults()` | `init_service.py` |
| Claude/Cursor allowlist management, Cursor/Copilot hook config (was `config/*_allowlist.py`, `config/*_hooks.py`) | `crossby.config.*`, `crossby.sync.permissions` | `implementation_service/bootstrap.py`, `init_service.py` |

Wade still owns a thin `services/ai_resolution.py` rather than delegating outright: wade's `ProjectConfig.ai` uses named per-command fields (e.g. `ai.plan`), while crossby's own config expects a `commands` dict — the two shapes aren't interchangeable yet. See that module's docstring for the up-to-date status.

Adding support for a new AI tool means contributing an adapter to crossby, not to this repo — see `docs/dev/extending.md`.

## Hook Guard Layer

wade installs AI-tool hooks that enforce session rules in *code* rather than
trusting the agent to follow the skill. The split across the two repos:

| Concern | Lives in | Detail |
|---------|----------|--------|
| Guard **policies** (allow/deny) | wade `hooks/policies.py` | Pure predicates over a normalized `HookEvent` |
| Guard **entry point** | wade `hooks/cli.py` (`wade-hook`) | Argparse, guard routing, dialect selection |
| Guard **installation** | wade `implementation_service/bootstrap.py` | Per-worktree, at `bootstrap_worktree` time |
| Hook **dialects** (parse/emit, tool names, events, capabilities) | crossby | `hooks/runtime.py`, `sync/hooks.py`, `ai_tools/*` |

### Guards

| Guard | Event | Failure mode | Blocks |
|-------|-------|--------------|--------|
| `worktree` | PreToolUse | **closed** (deny) | Writes resolving outside the worktree |
| `plan` | PreToolUse | **closed** (deny) | Writes to anything but plan artifacts |
| `session-complete` | Stop | **open** (allow) | Ending an impl/review turn with commits ahead of base and no current `done` marker (once) |
| `plan-complete` | Stop | **open** (allow) | Ending a plan turn with no valid `PLAN*.md` in `.wade/plans` (once) |

The asymmetry is deliberate and must not regress: a write guard that allows on
error is worse than useless, while a Stop guard that blocks on error traps the
agent in a session it cannot exit. Both Stop guards live in `hooks/policies.py`
as pure predicates fed a fact the `hooks/cli.py` Stop branch computes:
`session-complete` over `commits_ahead` + `done_marker_present` (git facts read
via **raw `subprocess`** — never the `wade.git` layer, whose `structlog` output
would corrupt the lean entry's decision-JSON contract), and `plan-complete` over
`has_valid_plan(.wade/plans)` (via a **lazy** import of
`wade.utils.plan_validation`, kept off the hot PreToolUse write path). Any
failure — unresolvable dir, missing `--root`, exception — fails open. The two
Stop guards share the single-shot `.wade/stop-nudged` marker because a worktree
is either a plan worktree or an impl worktree, never both. Installation:
`bootstrap_worktree` installs `session-complete` for impl/review sessions (plus
the pre-push `done` backstop) and `plan-complete` for plan sessions.

### Two write channels

A write reaches the guard through one of two channels, and both must be covered:

- **`file_path`** — a tool-call write (`Write`, `Edit`, `apply_patch`,
  `write_to_file`, …). crossby's `HookEvent.is_write` is a **denylist**
  (`READ_TOOL_NAMES`), so a tool name it has never seen counts as a write.
- **`command`** — a *shell* write. crossby reports `is_write=False` for shell
  tool names (`SHELL_TOOL_NAMES`) by design, so the file-path policies would
  allow it; `wade-hook` routes any payload carrying a `command` to
  `shell_containment` instead.

`shell_containment` tokenizes with `shlex.split` (failing **closed** on
unbalanced quotes) and contains **writes** while allowing **reads** anywhere:
reading a sibling repo (`cat ../crossby/x`, `grep -r foo ../crossby`,
`git -C ../crossby log`) never mutates state, so a read operand may resolve
outside the worktree. It denies output redirect targets (`>`, `>>`, `2>`), `<`
input redirects excepted (a read); `cd`/`pushd` targets; and operands of *write*
commands — `_PLAN_WRITE_COMMANDS` (`tee`/`cp`/`mv`/`touch`/`mkdir` …), git write
subcommands `_GIT_WRITE_SUBCOMMANDS` (`checkout`/`clean`/`clone`/`init`/`worktree`
…), and in-place editors (`sed -i`, `perl -i`) — that resolve outside. In plan
mode it additionally rejects those same writes when aimed at non-artifacts, and
denies the in-place `-i` flag outright.

Spaced `git -C <dir>` is buffered: `git -C ../crossby log` (a read subcommand) is
allowed, but a git *write* subcommand after an outside `-C` (`git -C /outside
clean -fd`, `git -C /outside checkout -- file`) is denied — there is no later path
operand to catch it otherwise. It also unglues paths from flags
(`--output=/etc/x`, `-o/etc/x`, `of=/etc/x`, `git -C/etc/other`) and keeps those
**glued** forms contained in every mode (a tokenizer cannot tell a glued read flag
from a glued write flag, so a few glued reads are denied too), treats bash's
`>&file` as a write while skipping true fd duplication (`2>&1`), denies a bare
`cd` (it lands in `$HOME`), and exempts character devices (`>/dev/null 2>&1`) plus
system temp dirs (`/tmp`, `$TMPDIR`) — shared scratch space where writes are always
allowed (plan mode still denies them as non-artifacts). The temp exemption is
scoped to `shell_containment` (via `_ALWAYS_ALLOWED_PATH_PREFIXES`); the file-path
guard `worktree_containment` stays strictly worktree-only.

**It is defense-in-depth, not a completeness guarantee.** It stops the
non-obfuscated cases an agent actually produces. Documented residual gaps
(see the function docstring): env-var indirection (`$HOME/x`), command
substitution, subshells, here-docs, `$IFS` tricks, `eval`, symlinks created
within the same command, and interpreters given inline code (`python -c`). The
read relaxation gives up a few more write-escapes rather than re-block the
sibling-repo reads it exists to allow: **wrapped** write commands (`sudo rm
/outside`, `env FOO=bar cp a /outside`, `xargs rm` — the wrapper hides the real
writer), **unenumerated** write commands (`zip`, `git bundle create /outside`,
`git format-patch -o /outside`), **spaced** output flags on non-write commands
(`curl -o /outside`), directory-context flags on non-git extractors/builders
(`tar -C /outside`, `unzip -d /outside`, `make -C /outside`), and conditional-write
`find` (`find ../outside -delete`). Only enumerated writers
(`_PLAN_WRITE_COMMANDS` / `_GIT_WRITE_SUBCOMMANDS` / in-place editors) and the
**glued** output-flag forms stay caught — the guard is best-effort defense-in-depth
against the writes an agent actually produces, not a security boundary.

### Per-tool capability matrix

Two static maps in `hooks/cli.py` mirror crossby's adapter capabilities. They
are **deliberate copies** — the hot per-edit path must not import
`crossby.ai_tools` (~450ms vs ~150ms cold start) — so they must be re-verified
on every crossby bump. `TestPerToolDialectsMatchCrossby` asserts they still
agree, turning silent drift into a test failure.

| Tool | PreToolUse dialect | Stop dialect | Notes |
|------|--------------------|--------------|-------|
| Claude | `hookSpecificOutput` | `{"decision":"block"}` | Extra root-level keys fail schema validation → silent fail-open |
| Codex | `hookSpecificOutput` | `{"decision":"block"}` | Sandboxes writes; guard narrowed to the shell token |
| Cursor | `{"permission":…}` | `{"followup_message":…}` | Only tool defaulting to fail-**open**; needs `failClosed` |
| Copilot | flat `{"permissionDecision":…}` | `{"decision":"block"}` | Never nests under `hookSpecificOutput`; `tools` scope is dropped, so its guard fires on everything |
| Antigravity CLI (`agy`) | `{"decision":…}` | `{"decision":"continue"}` | Inverted Stop polarity — blocks by saying *continue* |

Codex is the one tool whose worktree guard is **narrowed** rather than skipped:
`--sandbox workspace-write` already confines tool-call writes, but it also
permits `/tmp` and `$TMPDIR`, so a shell redirect remains a live escape.

### Upgrade path for already-inited projects

Guards are installed **per-worktree** in `bootstrap_worktree`, not at
`wade init` time, and nothing guard-related is persisted into a project at init.
So an existing inited project picks up corrected guards automatically on its
next `wade implement` / `wade plan` session once wade (and transitively crossby)
is upgraded — **no re-init, resync, or migration is needed.**

## Completion Gates & the `done`-marker

`done` (`implementation_service/done.py`) is the authoritative completion gate.
Both `implementation-session done` and `review-pr-comments-session done` call the
same `done()` service, parameterized by `session_type`; the gate set branches on
it and runs in a **fixed order** (a clean main-merge in the sync step advances
HEAD, so any sha-keyed check must precede it):

1. **PR-SUMMARY** (implementation) — present, non-empty, non-placeholder.
2. **unresolved-threads** (review-pr-comments only, runs *first* for that
   session type) — a transient provider error is non-blocking.
3. **review-ran** (both) — `marker_present(worktree, "reviewed", pre-sync HEAD)`.
   Checked against the pre-sync HEAD so a clean main-merge doesn't invalidate the
   review just performed. **Implementation sessions add a code-enforced review-pass
   cap (`done.max_review_passes`, default 2) (#384):** past the exact-sha fast
   path, the gate counts distinct
   `review-pass@<sha>` markers (written by each delegation-backed
   `wade review implementation`, independent of its success — so a headless
   timeout still counts) and, once `done.max_review_passes` (default 2) is
   reached, completes anyway with a notice rather than looping. `wade review
   implementation` surfaces the running budget after each pass ("review pass N of
   M — K left"), so the count is visible from the command rather than only from
   the skill prose or the `done`-time notice. A listdir failure
   counts as 0 — as does a symlinked `.wade` or a platform without descriptor-based
directory reads (fail closed toward re-gating, so tampering can't satisfy the
cap). `review-pr-comments` keeps the unbounded
   fast-path-or-refuse behavior — the gate is shared, so the cap branch is
   scoped to `session_type == "implementation"`.
4. **sync** (implementation only) — auto-sync via the existing `do_sync` service
   when `behind > 0`, refuse only on conflict.
5. `_done_via_pr` writes `.wade/done@<post-sync HEAD>` immediately before pushing.

The **done-marker primitive** lives in `utils/markers.py` — a pure-stdlib leaf
(so the lean `wade-hook` can import it cheaply). A marker is a zero-byte file
`.wade/<name>@<sha>` meaning "the `<name>` gates passed for `<sha>`"; a new commit
changes the sha and invalidates every prior marker (**missing/stale ⇒ not done**).
All reads/writes go through an `O_DIRECTORY | O_NOFOLLOW` handle on `.wade` so a
symlinked `.wade` can never redirect them — there is deliberately **no** path-based
fallback (following the symlink is worse than not writing). The same module backs
the single-shot `.wade/stop-nudged` flag (`flag_marker_*`), so the Stop-nudge and
done-marker implementations can't drift.

⚠ **`commits_ahead` argument order differs by call site** and is pinned by a test:
the sync gate's behind-count is `commits_ahead(repo, origin/<main>, branch)`
(base in the *branch* position); the Stop hook's ahead-count puts the session
branch in the branch position. Inverting either is a silent bug.

The **pre-push backstop** (`templates/hooks/pre-push`, installed by
`skills/installer.py:install_worktree_git_hook`) makes the gate hard to skip: git
runs it with cwd at the worktree top, so it tests `[[ -f ".wade/done@${sha}" ]]`
in pure shell. It is wired per-worktree via `extensions.worktreeConfig` +
`git config --worktree core.hooksPath .wade/githooks` (git ≥ 2.20; **graceful
degrade** to warn-and-skip otherwise), so it never leaks to the main checkout or
sibling worktrees. Because `core.hooksPath` *replaces* `.git/hooks`, the installer
detects any pre-existing hook once at first install, persists it to
`.wade/githooks/.chain`, and the wade hook **chains** to it (re-emitting the exact
buffered stdin) rather than silently shadowing it. The reusable installer core is
designed for #352 to add `pre-commit`/`commit-msg` hooks. **Honesty:**
`git push --no-verify` bypasses it in one flag — a quality/backstop layer, not a
boundary.

## Command Dispatch

`src/wade/cli/main.py` is the root Typer application. It registers subcommand groups (`task`, `worktree`, `plan-session`, `implementation-session`, `review-pr-comments-session`, `review`) and admin commands (`init`, `update`, `deinit`, `check-config`, `shell-init`). The `tasks` alias is registered as a hidden Typer group pointing to the same `task_app`. The `wade` entry point (defined in `pyproject.toml` as `wade.cli.main:cli_main`) invokes the root app.

CLI modules are **thin dispatch layers** — they parse flags via Typer, then call service methods. Business logic lives in `services/`, not in `cli/`.

**Interactive menus**: `wade task` and `wade worktree` with no subcommand show interactive menus. `wade task create` prompts interactively for title and body. Top-level commands `plan`, `implement`, `implement-batch`, and `cd` are registered directly on the root app. The `review` subcommand group provides `plan`, `implementation`, `pr-comments`, and `batch` commands. Hidden short aliases `p`, `i`, and `r` map to `plan`, `implement`, and `review pr-comments` respectively. The numeric shorthand `wade <N>` is rewritten to the hidden `smart-start` command in `cli_main()`, which detects PR state and routes to implement or review pr-comments.

**Shell integration**: `wade shell-init` outputs a shell function wrapper for `eval "$(wade shell-init)"` that intercepts `wade cd <n>` and `wade worktree cd <n>` to perform a real `cd` in the caller's shell.

## Config System

`config/loader.py` walks up from CWD to find `.wade.yml` and parses it via PyYAML into a `ProjectConfig` Pydantic model. The v2 config format has nested sections:

```yaml
version: 2
project:
  main_branch: main
  issue_label: feature-plan
  worktrees_dir: ../.worktrees
  branch_prefix: feat
  merge_strategy: PR
ai:
  default_tool: claude
  plan:
    tool: claude
    model: ""
  deps:
    tool: claude
  implement:
    tool: claude
models:
  claude:
    easy: claude-haiku-4.5
    medium: claude-haiku-4.5
    complex: claude-sonnet-4.6
    very_complex: claude-opus-4.6
provider:
  name: github
hooks:
  post_worktree_create: scripts/setup-worktree.sh
  copy_to_worktree:
    - .env
  pre_commit:                # opt-in repo-quality gate (#352); off by default
    lint: ./scripts/check.sh --lint
    test: ./scripts/test.sh
  commit_msg:
    conventional: true       # validate Conventional Commits on `git commit`
  post_tool_use:
    enabled: false           # in-turn lint feedback to context-capable tools
    lint_cmd: ruff check      # FILE-SCOPED (edited path appended); else pre_commit.lint whole-repo
    timeout: 10
knowledge:
  enabled: true
  path: KNOWLEDGE.md
done:                        # completion-gate toggles (all default true)
  require_pr_summary: true
  require_sync: true
  require_review: true
  require_resolved_threads: true
  pre_push_backstop: true
  max_review_passes: 2       # impl-session review→fix loop cap (#384); strict positive int
```

**`done` section** (`DoneConfig`): completion-gate escape hatches, all default
on. Per knowledge `ca245d6a`, config-key validity lives in **three** places that
can drift — the Pydantic model (`models/config.py`), the loader
(`config/loader.py`), and the `check_service.py` validator. The `done` validator
allowlist is **derived** from `DoneConfig.model_fields`, so a new field is
accepted automatically.

**Model complexity mapping**: The `models` section maps AI tool names to complexity-tiered model IDs (`easy`, `medium`, `complex`, `very_complex`). When `wade implement` is invoked, the service reads the `complexity:X` label from the issue (falling back to `## Complexity` in the body), maps it to the appropriate configured model, and passes it as `--model` to the AI tool — unless the user explicitly passed `--model` themselves.

**Per-command AI tool and model overrides**: The `ai` section supports `plan`, `deps`, `implement`, `review_plan`, `review_implementation`, and `review_batch` sub-sections, with optional `tool`, `model`, `mode`, `effort`, `enabled`, `yolo`, `permission_mode`, and `timeout` keys as applicable. `timeout` bounds a headless subprocess (seconds) and defaults to **600s** (`DelegationRequest.timeout`) — large enough for a high-effort review/deps run over a big diff to finish rather than tripping mid-run. The fallback chain is: CLI `--ai`/`--model` flag -> command-specific config -> global `default_tool`. This is implemented in `ProjectConfig.get_ai_tool(command)` and `ProjectConfig.get_model(command)`. When `mode` is omitted, `review_plan` and `review_implementation` default to `prompt`, while `review_batch` defaults to `interactive`.

**Permission (autonomy) mode vs. delegation `mode` — two orthogonal axes**: The `mode` key (`DelegationMode`: `prompt`/`interactive`/`headless`, `models/delegation.py`) governs *how* a tool is dispatched. `permission_mode` (`PermissionMode`: `default`/`accept-edits`/`auto`/`yolo`, `models/permission.py`) governs *how much* the tool may do without prompting — the autonomy axis crossby exposes via the `yolo`/`auto`/`accept_edits` launch booleans. Do **not** conflate them: they live in separate modules on purpose. Resolution (`resolve_permission_mode()` in `ai_resolution.py`) follows CLI `--permission-mode` > `--yolo` alias > command config > global config > `default`; `permission_mode` wins over the legacy `yolo` alias at any level, and `get_yolo()`/`resolve_yolo()` are thin shims that derive from the resolved mode so the alias has a single source of truth. WADE forwards only the *requested* tier and does **not** gate on per-tool capability — crossby owns capability-aware downgrades and warnings (`_autonomy_launch_args`), so `auto` on a non-Claude tool downgrades to `accept-edits` instead of WADE silently disabling it. The headless delegation path always forces `default` (no autonomy grant) regardless of config, since `deps`/`review_*` are read/analytical. `plan` is intentionally excluded from `PermissionMode` (WADE drives plan mode separately via `plan_service` → `plan_mode=True`); a configured or CLI-supplied `permission_mode: plan` (or any invalid value) warns and falls back to `default`.

**Worktree hooks**: The `hooks` section lets projects run setup automatically when a worktree is created. `post_worktree_create` points to a script that runs in the new worktree (e.g., installing dependencies). `copy_to_worktree` lists files to copy from the project root into the worktree before the hook runs (e.g., `.env`). Hook failures are non-fatal — a warning is logged and the session continues.

**Repo-quality gates** (`HooksConfig` → `PreCommitConfig` / `CommitMsgConfig` / `PostToolUseConfig`, #352): three opt-in, off-by-default subsections. `pre_commit.{lint,test}` and `commit_msg.conventional` install per-worktree `pre-commit` / `commit-msg` git hooks (baked from config at bootstrap via placeholder substitution — no per-commit config load). They are reconciled together with the `done.pre_push_backstop` `pre-push` hook by `reconcile_worktree_git_hooks`, which installs the desired set in one batch (so every prior user hook is captured **before** wade sets `core.hooksPath` — per-hook `.chain-<hook_name>` files; a #349 unsuffixed `.chain` is migrated to `.chain-pre-push` on upgrade) **and** is idempotent across re-bootstraps of a reused worktree: a gate turned off since a prior session is neutralized (a chain-only passthrough that still runs any captured prior, or a full uninstall + `core.hooksPath` unset when nothing is managed), so disabling a gate actually disables it. `post_tool_use` installs a PostToolUse hook into context-capable tools only (dialect ≠ `DECISION`, so Antigravity CLI is skipped and its prior entry removed); the command is **stable** (`wade-hook post_tool_use --tool <id> --root <root>`) and resolves `lint_cmd`/timeout/scope from `.wade.yml` at runtime, so re-bootstrap dedups, a disabled gate's entry is removed (and a leftover hook self-noops), and it fails open — lints the just-edited file (`lint_cmd` file-scoped; falls back to `pre_commit.lint` whole-repo) and injects findings back as `additionalContext`, never blocking. All three, like `done`, derive their `check_service` validator allowlists from `*.model_fields` so config-key validity can't drift (knowledge `ca245d6a`). **Honesty:** `git commit --no-verify` bypasses the git hooks — these are quality gates, not enforcement boundaries.

**Project knowledge** (worktree-local lifecycle, #358): The optional `knowledge` section enables a project knowledge file (`KNOWLEDGE.md`) for cross-session AI learning, with an append-only ratings **vote log** (`KNOWLEDGE.ratings.jsonl`) beside it. Both are **tracked** files — a session edits the copy in the worktree it is standing in, and the change rides to `main` with its PR. They are **not** copied into worktrees (a copy would manufacture a stale snapshot); `_resolve_knowledge_root` keys off the HEAD-attachment state, redirecting only a throwaway detached-HEAD worktree (a `plan` / `task deps` session) back to the main checkout. Two mechanisms make concurrent branches merge cleanly: a wade-managed `merge=union` block in `.gitattributes` (`ensure_knowledge_merge_attributes`, ensured per attached bootstrap; committed so `main` carries it as the server-side backstop), and the append-only vote log (merging is concatenation → no vote lost). `wade knowledge add` appends an entry (blocked in a throwaway plan/deps session — no PR to carry it), `rate` appends an up/down/stale vote (allowed everywhere; a throwaway session's vote is carried into the next attached session's PR by a ratings-only reconcile at bootstrap), `get` prints the annotated file, and `status` reports uncommitted knowledge/ratings changes scoped to just those paths. A pre-#358 `KNOWLEDGE.ratings.yml` is folded to the same scores on read (in memory, no write) and converted on the first ratings write to a byte-deterministic seeded `.jsonl` (the `.yml` is `git rm`'d). A `.wade.yml` migration strips the knowledge/ratings entries from any existing `hooks.copy_to_worktree` (paths are canonicalized before comparison — folding `.`/`..` — so `./KNOWLEDGE.md`, `docs/../KNOWLEDGE.md` and `KNOWLEDGE.md` all match; bootstrap's copy-exclusion applies the same `collapse_relative_path` policy so a redundant-`..` spelling can't slip past one and re-copy main's file). The path must stay inside the project root. The pure path/parse/validation helpers (`resolve_knowledge_path`, `resolve_ratings_path`, `parse_entries`, `validate_knowledge_file`) live in the leaf module `utils/knowledge_file.py` so lower layers can use them without importing the service; `done` runs `validate_knowledge_file` as a completion gate (refuses on duplicate entry IDs or unresolved conflict markers when `knowledge.enabled`) so a `merge=union`-corrupted file can't reach `main`. (Attached-session knowledge edits are worktree-local, so an attached session never dirties `main`. The one exception is a detached `plan` / `task deps` session: its `rate` vote appends to main's tracked ratings JSONL and stays uncommitted there until the next attached bootstrap's ratings-only reconcile carries it forward and restores main. The stash/pop branch in `_pull_main_after_merge` therefore still matters — it covers that transient detached-ratings dirt as well as non-knowledge dirt.)

## Config Migration Pipeline

`config/migrations.py` provides a single migration run during `wade update`:

| # | Function | What it does |
|---|----------|-------------|
| 1 | `ensure_version(raw)` | Set `version: 2` if missing |

`run_all_migrations(config_path)` loads YAML, runs the migration, writes back only if changed. Returns `True` if the file was modified.

## Update Flow

`wade update` performs 12 steps (see the `update()` docstring in
`init_service/commands.py`):

1.  Self-upgrade check (runs before project validation; see below)
2.  Validate repo + config existence
3.  Read old version from manifest
4.  Show version transition message
5.  Run config migration pipeline
6.  Reload config + backfill probed models
7.  Warn about removed AI tools still referenced in config
8.  **Migrate** — remove old skill files from main (skills now live in worktrees only)
9.  **Migrate** — remove the stale committed `.gitignore` block, if present
10. Make `.wade/` self-ignoring (idempotent)
11. **Migrate** — remove AI-tool + leftover Gemini artifacts from the main checkout
12. Rebuild manifest with version (no skills on main)

Steps 8, 9, and 11 are one-way **migrations that *remove*** artifacts from main —
earlier wade versions committed skills, a `.gitignore` block, and AI-tool files
there; those now live only in per-session worktrees (installed by worktree
bootstrap — see `docs/dev/skills-system.md`). `wade update` does **not** install
or refresh skills, allowlists, or the `AGENTS.md` pointer; that is bootstrap's job,
run per session by `wade implement`/`wade plan`/`wade review` (and by a standalone
`wade task deps`).

**Self-upgrade mechanism**: `utils/install.py:detect_install_method()` inspects `sys.executable` to determine how wade was installed (`uv-tool`, `pipx`, `brew`, or `editable`). On `wade update`, `self_upgrade()` runs the appropriate package manager command (e.g. `uv tool upgrade wade`), then `re_exec()` replaces the current process via `os.execv()` so the new code is loaded. Editable installs skip this naturally. Pass `--skip-self-upgrade` to bypass.

## AI Interaction Pattern

All AI-interactive commands follow the same pattern:

1. **Tool selection** — If no `--ai` flag is given, use the tool from config (via `ProjectConfig.get_ai_tool()`). If that's empty, prompt the user interactively via `ui/prompts.py`.
2. **Initial prompt** — Build the starter prompt and display it in a console panel. It is passed directly to the AI tool as an initial message on launch (no clipboard involved).
3. **Launch AI CLI** — Execute the AI tool binary via `AbstractAITool.launch()`. The tool runs interactively in the terminal with the prompt pre-filled.
4. **Post-AI processing** — After the AI CLI exits, the service picks up where it left off (e.g., detecting new issues, parsing output files, capturing token usage from transcripts).

Each AI tool adapter must implement the abstract method `capabilities()` (binary name, model flag syntax, headless flag). All other methods — `initial_message_args()`, `launch()`, `parse_transcript()`, `is_model_compatible()`, and `build_launch_command()` — have default implementations and can be overridden as needed. The `launch()` method accepts an optional `transcript_path: Path | None` parameter — when provided, the adapter captures session output to that file for post-session token usage extraction. When adding a new AI-interactive command, follow this existing pattern.

**Deps delegation modes**: `deps_service.py` runs analysis via the generic delegation infrastructure (`delegation_service.py`). The default mode is `headless`; it can be overridden to `interactive` or `prompt` via the `ai.deps.mode` config key or the `--mode` CLI flag. There is no automatic fallback between modes — the resolved mode is used directly. Prompt mode prints the raw dependency-analysis prompt with no AI-tool requirement or worktree bootstrap. Headless and interactive modes perform the real AI launch path and are the only modes that create the temporary analysis worktree.

## Issue Detection (Snapshot/Diff Pattern)

`wade plan` uses a snapshot/diff pattern to detect issues created during an AI session (Path A — fallback):

1. **Before AI** — Snapshot all open issue numbers with the configured label
2. **AI runs** — The agent creates issues via `wade task create` from within the AI CLI
3. **After AI** — Compare current issue numbers against the pre-snapshot, returning only newly created ones

This avoids requiring the AI to report back which issues it created — the service detects them deterministically. When no issues are detected (Path B), the service reads plan files from the session temp dir and creates lightweight issues + draft PRs.

## Merge Strategy

`MergeStrategy` (config key `project.merge_strategy`) has a single value, `PR` — the `direct` strategy was retired in #357. A config that still carries `merge_strategy: direct` is migrated to `PR` with a warning on load (`config/loader.py` `_migrate_merge_strategy`), and `wade check-config` rejects it.

- **`PR`** (default and only) — The agent runs `wade implementation-session done` during its session to push the branch and update the existing draft PR (or create one if missing). The worktree is **not** cleaned up by `done` — it is cleaned up automatically by `implement` after the human merges the PR. When the tool exits, `implement`'s post-work prompt detects the PR and asks "Do you want to merge this PR?" — if yes, squash-merges via `gh pr merge --squash --delete-branch`.

`wade implementation-session done` handles PR creation / update. The post-work lifecycle prompt handles the merge decision.

## Determinism via Services

All deterministic operations — git commands, state transitions, file manipulation, API calls — **must live in service/utility code**, never in AI agent reasoning. Agents are non-deterministic; code is deterministic. The boundary is:

- **Code decides and executes** — fetch, merge, branch creation, worktree lifecycle, issue state changes. These are codified in `services/`, `git/`, `providers/` and exposed via `wade <command>`.
- **Agents interpret and decide next steps** — reading conflict diffs, choosing resolution strategies, composing commit messages, deciding whether to proceed. These are guided by skills.

When adding new functionality, ask: "Can an AI agent get this wrong by reasoning about it?" If yes, put it in code. Examples:

| Deterministic (code) | Non-deterministic (agent) |
|------------------------|---------------------------|
| `git merge main --no-edit` | Resolving merge conflicts |
| Checking if worktree is clean | Deciding what to commit |
| Creating branch with naming convention | Writing commit messages |
| Emitting structured JSON events | Interpreting event output |

This is why `wade implementation-session sync` exists as a CLI command rather than instructions for agents to run raw git commands — the sequence (preflight -> fetch -> merge -> conflict detection -> event emission) is deterministic and must not vary between agent sessions.

When wade installs skills into a target project (per session, via worktree bootstrap), the skills reference `wade <command>` — they do **not** bundle standalone copies of the logic. The wade CLI is the single source of truth for deterministic operations.

## CLI Flag Reference

**`wade implement`:**
- `--detach` — Launch AI in a new terminal tab/window (non-blocking). Uses `build_launch_command()` + `launch_in_new_terminal()`.
- `--cd` — Create worktree, print its path to stdout, and exit without launching AI. Deterministic setup still runs first (for example worktree bootstrap and draft-PR bootstrap when needed). Used internally by `wade cd`.

**`wade implementation-session done`:**
- `target` (positional) — Optional issue number, worktree name, or plan file path. When a file path is given, creates the issue first; when a number/name, finds the worktree; when omitted, detects from current branch.
- `--no-close` — Don't close the issue on merge.
- `--draft` — Create PR as draft.

**`wade implement-batch`:**
- `--model` — Pass a specific AI model to all parallel sessions.

**`wade worktree remove`:**
- `--all` — Hidden alias for `--stale` (removes all stale worktrees).

**`wade update`:**
- `--skip-self-upgrade` — Skip the source-version self-upgrade check.

**`wade task create`:**
- No flags required — prompts interactively for title and body.

**`wade shell-init`:**
- No flags. Outputs a shell function for `eval "$(wade shell-init)"`.

## Dependencies

- **Python** 3.11+ (uses `StrEnum`, `|` union syntax, `from __future__ import annotations`)
- **git** 2.20+ (worktree commands)
- **gh CLI** — must be authenticated; needs `project` scope for board moves
- **uv** — recommended for development (manages virtualenv and dependencies)

### Python Package Dependencies

Runtime:
- `typer>=0.12` — CLI framework
- `pydantic>=2.0` — Data validation and settings
- `pydantic-settings>=2.0` — Env var overrides
- `pyyaml>=6.0` — YAML config parsing
- `rich>=13.0` — Terminal UI (tables, prompts, panels)
- `questionary>=2.0` — Interactive prompts (select, confirm, input)
- `structlog>=24.0` — Structured logging
- `httpx>=0.27,<1.0` — HTTP client (ClickUp provider)

Dev:
- `pytest>=8.0` — Test framework
- `pytest-cov>=5.0` — Coverage reporting
- `mypy>=1.10` — Static type checking (strict mode)
- `ruff>=0.4` — Linting and formatting
- `pre-commit>=3.7` — Git hook management
- `types-PyYAML>=6.0` — Type stubs for PyYAML
