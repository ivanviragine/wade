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
│   ├── plan_session.py  # plan-session check/done
│   ├── deps_session.py  # deps-session check (actual detached-agent runtime)
│   ├── hook.py          # `wade hook` — write-guard entry point for AI tool hooks
│   └── autocomplete.py  # Shell autocompletion helpers
├── models/              # Pydantic domain models (pure data, no I/O)
│   ├── config.py        # ProjectConfig, ProjectSettings, AIConfig, ComplexityModelMapping
│   ├── task.py          # Task, PlanFile, Complexity, Label, TaskState
│   ├── session.py       # ImplementationSession, WorktreeState, SyncResult, SyncEvent, MergeStrategy
│   ├── delegation.py    # DelegationRequest, DelegationResult, DelegationMode
│   ├── workflow.py      # Canonical session/delegation definitions and mappings
│   ├── skill.py         # Skill refs, descriptors, slots, and binding components
│   ├── session_manifest.py # Frozen session/delegation/review/doc state models
│   ├── permission.py    # PermissionMode (autonomy axis), launch-kwargs helpers
│   ├── readiness.py     # phase capability requirements + stable failure IDs
│   ├── review.py        # PRReviewStatus, ReviewThread, ReviewComment
│   ├── batch.py         # BatchIssueContext, BatchReviewContext
│   ├── deps.py          # DependencyEdge, DependencyGraph
│   └── events.py        # Typed event models
├── services/            # Business logic (orchestration)
│   ├── task_service.py  # Task CRUD, plan parsing, labels
│   ├── implementation_service.py  # Implementation session lifecycle
│   ├── plan_service.py  # AI planning sessions
│   ├── review_service.py           # PR review session lifecycle
│   ├── bot_trigger.py   # Marker-aware external-bot review triggering (done + menus)
│   ├── review_delegation_service.py # AI-powered review delegation
│   ├── session_composition_service.py # Resolve/freeze/refresh interactive bindings
│   ├── skill_invocation_service.py # Bind methods to bounded operation contracts
│   ├── skill_diagnostics_service.py # Discovery and precedence diagnostics
│   ├── review_record_service.py # Binding-aware durable review outcomes
│   ├── documentation_receipt_service.py # Current-commit documentation decision
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
│   ├── markdown.py      # MarkdownIssueProvider (single central task file)
│   ├── _pr_delegate.py  # GitHubPRDelegateMixin (routes PR/review APIs through gh)
│   └── registry.py      # Provider registry (register_provider / get_provider)
├── git/                 # Git operations (all subprocess)
│   ├── repo.py          # Repo introspection
│   ├── worktree.py      # Worktree create/remove/list
│   ├── branch.py        # Branch naming, creation, deletion
│   ├── sync.py          # Fetch + merge, conflict detection
│   ├── pr.py            # PR creation, merge
│   └── hooks.py         # Per-worktree git-hook install/reconcile (core.hooksPath, prior-hook chaining)
├── hooks/               # Guard policies (invoked via `wade hook` / `wade-hook`)
│   ├── cli.py           # Lean `wade-hook` entry point (dialect maps, guard routing)
│   └── policies.py      # worktree_containment / plan_artifact_only / shell_containment / session_complete
├── skills/              # Skill discovery, resolution, and file management
│   ├── catalog.py       # Replaceable built-in methodology catalog
│   ├── discovery.py     # Crossby-backed worktree/main project inventory
│   ├── resolver.py      # Explicit builtin/project/path ref resolution
│   ├── validation.py    # Recursive safety validation and stable digests
│   ├── materializer.py  # Atomic session/operation physical snapshots
│   ├── installer.py     # Tool-native support projections
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
    ├── conventional.py  # Canonical conventional-commit TITLE validator (types + is_conventional_title) — stdlib-only, single Python source of truth
    ├── plan_validation.py # Lean plan-file validator (discover/validate/has_valid_plan) — Stop-path safe; sources its title regex from conventional.py
    ├── process.py       # Subprocess helpers
    ├── http.py          # HTTPClient for REST API providers
    ├── markers.py       # sha-keyed .wade/<name>@<sha> completion markers (done, reviewed, stop-nudged)
    ├── update_check.py  # Version checking, self-upgrade hints
    ├── install.py       # Self-upgrade helpers (venv/source detection, re-exec)
    └── templates.py     # Packaged prompt/skill/workflow/hook asset resolution
```

`templates/workflows/` contains fixed interactive lifecycle templates and
workflow-owned references. Replaceable methodology remains under
`templates/skills/`. See [Workflows and Dynamic Skills](workflows-and-skills.md)
for the ownership boundary, typed registries, precedence, manifests, and review
record model; see [Skills, Snapshots, and Pointer System](skills-system.md) for
discovery, support projection, and filesystem rules.

> `templates/hooks/pre-push` is the completion-gate backstop script installed
> per-worktree at `.wade/githooks/pre-push` (see *Completion Gates & the
> `done`-marker* below).
>
> **There is no `db/` package** (hence its absence from the tree above). Earlier
> wade versions scaffolded a SQLite layer, but no code path under `services/` or
> `cli/` ever wrote session/worktree/PR rows. The package and its sole reader — a
> `SessionRepository.get_by_worktree_path` lookup in
> `implementation_service/cleanup._preserve_session_data`, which always returned
> empty — were removed in **#357 C5**; that function now detects the AI tool by
> worktree directory presence alone. Real persisted state lives in GitHub
> (PR/issue body markers, labels) and worktree files — never SQLite.

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
| `worktree` | PreToolUse | **closed** (deny) | Writes resolving outside the worktree (except the active tool's memory subtree and always-allowed scratch — see [Memory allowlist](#memory-allowlist) and below) |
| `plan` | PreToolUse | **closed** (deny) | Writes to anything but plan artifacts (the memory subtree and always-allowed scratch are also exempt) |
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
mode it additionally rejects those same writes when aimed at non-artifacts
(except always-allowed scratch — see below), and denies the in-place `-i`
flag outright.

Every git directory-redirect flag is buffered the same way, in every spelling
git's own parser (`git.c`) accepts: `-C`, `--work-tree`, and `--git-dir` are
functionally equivalent for this purpose (all three redirect where git
reads/writes), each spaced (`-C <dir>`) or glued/`=`-joined (`-C<dir>`,
`--work-tree=<dir>`) — including a relative, slash-less glued `-C` form like
`-C..`. `git -C ../crossby log` / `git --work-tree ../crossby log` (a read
subcommand) is allowed, but a git *write* subcommand after one of these
pointed outside (`git -C /outside clean -fd`, `git -C/outside clean -fd`,
`git --work-tree /outside clean -fd`, `git --git-dir=/outside clean -fd`) is
denied — there is no later path operand to catch it otherwise. Checked with
**strict** `_within` against `worktree_root` only, never `allow_paths` and
never the always-allowed-scratch exemption described below: a write reached
through any of these six spellings can touch every file under `<dir>`, not
just a direct memory/scratch write, so they stay strict even when `<dir>` is
the active tool's own memory root **or** a system temp dir (`git -C /tmp/x
clean -fd` is denied, even though a direct write to `/tmp/x` is scratch-exempt).

The identical blast radius is reachable the plain way, without any `-C` flag at
all: a `cd`/`pushd` to a directory outside `worktree_root` (necessarily
always-allowed scratch — a non-scratch outside target already denies at the
`cd`-target check) redirects git's implicit working directory for the rest of
the *whole command line*, not just the current segment — a real shell's cwd
persists across `&&`/`;`/`|`, unlike a per-invocation `-C` flag. So `cd /tmp/x
&& git clean -fd` gets the same denial as `git -C /tmp/x clean -fd` even though
it has no `-C` flag and `clean` has no path operand of its own for the
enumerated-write-command check to catch. `cwd_outside_root_token` buffers this
across segments (unlike the git-directory-redirect buffer, which resets per
segment) and is cleared by a later `cd` that lands back inside root. A
**same-segment** directory-redirect flag that itself resolves inside root
overrides the buffered scratch `cd` for that one invocation, exactly like a
real shell would: `cd /tmp/x && git -C /repo/wt clean -fd` is allowed, since
git's own `-C` takes precedence over the shell's cwd. This is tracked
separately (`git_dir_redirect_seen_in_root`) from the outside-root buffer,
because "no redirect flag this segment" and "redirect flag present and
in-root" both leave the outside-root buffer unset but must be told apart.

An accepted `cd`/`pushd` also rebases the tokenizer's `base` (the directory
later relative paths resolve against) to the new cwd — not just the
`cwd_outside_root_token` bookkeeping above. Without this, a *relative*
directory-redirect flag after a scratch `cd` (`git -C .`, `--work-tree .`,
`--git-dir .`) would resolve against the stale original `base` and wrongly
read as in-root, bypassing the same-blast-radius denial `cd /tmp/x && git
clean -fd` already gets. `base` is rebased only in the `cd`/`pushd` handler,
never by a directory-redirect flag itself, so a same-segment `-C` still
overrides containment for that one git invocation without permanently
changing where later, unrelated tokens resolve. The outside-root buffer is
**sticky within a segment**, not "last flag wins": once any
`-C`/`--work-tree`/`--git-dir` occurrence resolves outside root, a later
occurrence resolving in-root does *not* clear it (`git -C /tmp/x -C /repo/wt
clean -fd` stays denied). A naive reset was tried and reverted — repeated
relative `-C` values chain from the *preceding* `-C`, not the segment's
original cwd, so `git -C /tmp/x -C . clean -fd`'s second flag can resolve
back to root while git's real effective directory is still outside; and
`--work-tree`/`--git-dir` are independent settings, not last-one-wins
alternatives, so an in-root `--git-dir` must not clear an outside
`--work-tree`'s denial. Correctly resolving the narrower, legitimate
`-C a -C b` case needs per-flag-type effective-directory tracking, not a
shared token — out of scope for now; staying strict is the safe trade-off.

It also unglues paths from other flags
(`--output=/etc/x`, `-o/etc/x`, `of=/etc/x`) and keeps those **glued** forms
contained in every mode (a tokenizer cannot tell a glued read flag from a glued
write flag, so a few glued reads are denied too), treats bash's
`>&file` as a write while skipping true fd duplication (`2>&1`), denies a bare
`cd` (it lands in `$HOME`), and exempts known discard/console devices
(`>/dev/null 2>&1`) plus system temp dirs (`/tmp`, `$TMPDIR`) — shared scratch space
where writes are always allowed, in **every** mode. `_ALWAYS_ALLOWED_DEVICES` and
the temp-dir prefixes (`_ALWAYS_ALLOWED_PATH_PREFIXES`) stay **separate constants**,
unioned by `_is_always_allowed_scratch` and consulted together by `_contained`: a
device persists nothing (matched by an **exact** allowlist — `/dev/null`,
`/dev/zero`, … — not a `/dev/` prefix, since Linux mounts writable filesystems under
`/dev/` too, e.g. `/dev/shm` tmpfs, `/dev/mqueue`, where a write persists a real file
outside the worktree and a bare prefix would let `tee /dev/shm/out` escape), while a
temp write persists a real file (matched by prefix). Both are exempt from the
plan-mode plan-artifact rule for the same reason devices always were: the accepted
risk is a compromised plan session (no shell execution) staging bytes in `$TMPDIR`
that a later impl session (which has shell execution) could read or execute —
judged acceptable because system temp is world-shared scratch already reachable by
any local process, and impl mode already allowed it. The scratch exemption
(`_is_always_allowed_scratch`) is shared by `shell_containment` **and** the
file-path guards (`worktree_containment`, `plan_artifact_only`, both via
`_contained`) — the two channels no longer diverge on what counts as always-allowed
scratch. (Rule 6's git directory-redirect buffering is the sole exception, kept
**stricter** than a direct write even for a temp `<dir>` — see above.)

`_is_always_allowed_scratch` matches temp dirs **by prefix only**, never the
bare dir itself — a separate, narrow `_is_temp_root` predicate lets
`cd`/`pushd` land on the bare temp dir (`cd /tmp`, pure navigation), but
write-command operands, redirect targets, and the file-path channel
deliberately stay prefix-only, so a destructive command can't target the whole
shared directory (`rm -rf /tmp` stays denied). A version of the exact-dir
concession folded into the general scratch predicate was tried and reverted
for exactly this reason.

The **plan-artifact exemption specifically** (not baseline containment) uses a
stricter, `worktree_root`-aware variant, `_is_scratch_outside_worktree`, rather
than the plain `_is_always_allowed_scratch`. If `worktree_root` itself resolves
under a system temp dir — an ephemeral clone, a CI job, or a configured temp
worktree directory — every in-worktree path also matches the temp-prefix test,
so the plain scratch check would wrongly exempt ordinary in-worktree source
writes from the plan-artifact allowlist. `_is_scratch_outside_worktree` only
exempts a target that is both always-allowed scratch **and** resolves outside
`worktree_root`. Baseline containment doesn't need this: a path inside
`worktree_root` is already allowed via plain containment regardless of the
scratch check's order.

### Memory allowlist

All three write guards take an `allow_paths` tuple — the active tool's memory
allow-root — resolved by `_memory_allow_paths(tool, worktree_root)` in
`hooks/cli.py`. A write whose resolved path lands inside it is permitted
despite containment, and in plan mode is exempt from the plan-artifact rule
(checked **before** `_is_plan_artifact_path`, which reports any out-of-root
path — memory included — as a non-artifact). The resolved path is tool-specific
and, where the tool's own storage layout allows it, scoped to *this* worktree's
encoded project directory — but the three tools do **not** get the same
guarantee:

- **Claude** — `<config-home>/projects/<encoded-worktree>/memory/`. Sibling
  session transcripts live un-nested at `<encoded-worktree>/`, so the allow-root
  stops at `memory/`, not its parent. The only one of the three scoped to both
  this session **and** memory alone.
- **Cursor** — `<config-home>/projects/<encoded-worktree>/`. Its per-project dir
  *is* the memory location (no separate `memory/` subfolder to narrow to), but
  crossby's own reader globs that same directory for session-transcript JSON —
  so this is scoped to *this session's project* (narrower than the old code,
  which allowlisted every Cursor project on the machine) but not to memory
  alone: a guarded Cursor session can also rewrite/delete its own transcripts.
- **Codex** — `<config-home>/sessions/` — rollouts are filed by date, not by
  project, and shared flat across *every* project on the machine (crossby
  filters by a `cwd` field inside each file, not by directory). Unlike
  Claude/Cursor, this is **not scoped to this session at all** — every other
  project's Codex rollouts are writable too. Accepted because Codex's storage
  has no per-project boundary to key on; the alternative is dropping Codex from
  the allowlist entirely (like Copilot/Antigravity-CLI), not approximating a
  guarantee it cannot provide.

`<encoded-worktree>` mirrors the tool's own CWD-to-directory-name encoding
(`_encode_claude_project_path` / `_encode_cursor_project_path` in `hooks/cli.py`,
duplicated from `crossby.ai_tools.claude`/`cursor` rather than imported, for the
same lean-hot-path reason as the dialect maps below) — `worktree_root` is
**canonicalized (`.resolve()`) before encoding**, so a `worktrees_dir` reached
through a symlink still encodes the same physical path the launched tool
observes as its own CWD, not the symlink spelling. `<config-home>` is
`_tool_config_home`: it honors each tool's data-home relocation env var
(`CLAUDE_CONFIG_DIR`, `CODEX_HOME`) before falling back to `Path.home() /
".<tool>"` — without this, a relocated config dir (e.g. the isolated
`CLAUDE_CONFIG_DIR` `scripts/test-live-ai-taskr.sh` sets up for live tests) would
leave the tool's *real* memory writes denied. Copilot / Antigravity-CLI keep
memory in-repo, so they resolve to an intentional empty tuple (no bypass). It is
threaded into **redirect targets** and **write-command operands** on the shell
channel; `cd`/`pushd` and every git directory-redirect flag (`-C` spaced or
glued, `--work-tree=`, `--git-dir=`) stay strict — checked only against
`worktree_root`, never `allow_paths` — since a write reached through any of
them can touch every file under the target directory, not just a direct
memory write.

The allowlist is **deliberately narrow — never the tool's config/auth home**
(`~/.claude/settings.json` holds the `hooks` block these guards depend on;
allowlisting all of `~/.claude`, or even all of `~/.claude/projects`, would let
a session strip its own guard) **— though not uniformly narrow across tools**:
Claude and Cursor scope to the encoded, per-worktree leaf (also meaning an
ancestor-directory symlink cannot widen the exception the way a shared parent
directory could); Codex does not, per above. The leaf itself is never resolved
through a symlink either — `_memory_allow_paths` resolves only the leaf's
*parent*, then reattaches the leaf name literally, so a symlink swapped in for
`memory/` (or the encoded project dir, or `sessions/`) cannot silently
redirect the allow-root to whatever it points at; a write reaching such a
symlinked leaf resolves (via `_resolve_path`) to the real target, which no
longer falls under the allow-root and stays denied. The tool's config/auth files
(`~/.claude/settings.json`, `~/.codex/*state*.json`, `~/.cursor/*config*.json`)
stay denied regardless. `_memory_allow_paths` degrades safely — an unrecognized
tool, an intentional empty policy, an unresolvable
`Path.home()` (HOME unset), or a path that will not resolve all return `()` and
never raise, so containment behaves exactly as before. Like the dialect maps,
`_TOOL_MEMORY_DIRS` (now a plain key set, not a path map) is kept off the hot path
(no `crossby.ai_tools` import); `TestPerToolMemoryDirsCoverHookWriters` fails if
its key set drifts from `_hook_writers`. These per-tool memory locations
ultimately belong in crossby's `AIToolCapabilities` (mirrored wade-side today) —
a follow-up, not on this path.

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

Codex is the one tool whose worktree guard is **narrowed** rather than skipped,
and only under `ai.sandbox: true`: `--sandbox workspace-write` already confines
tool-call writes, but it also permits `/tmp` and `$TMPDIR`, so a shell redirect
remains a live escape. Under the unrestricted default the full matcher is
installed instead — see the sandbox-profile section below.

### AI runtime sandbox profile & sandboxed-worktree git writes (#423, #478)

A linked worktree's git metadata lives **outside** the worktree tree
(`<main>/.git/worktrees/<wt>` private dir, `<main>/.git` common dir), so under
`--sandbox workspace-write` every git write to it (`index`/`index.lock`, refs,
objects) is denied — `git add`/commit/stash/ref-update and `wade sync`/`done`
fail. WADE fixes this by threading the absolute **`working_dir`** (the worktree
path) into every session launch/resume builder; crossby's Codex adapter resolves
the out-of-root metadata dirs from it and grants them with additive `--add-dir`
(only dirs containing `HEAD`, rejecting crafted gitlinks). The OS sandbox stays
on and shell containment is **not** widened — only these two metadata roots are
added. The six threaded sites are the impl detached/inline × initial/resume and
review detached/inline launches (`implementation_service/core.py`,
`review_service.py`); `delegation_service`/`plan_service` thread the same
`working_dir` for their worktree-capable paths, under the same network constant
as everything else — there is no network-off launch path left.

**One axis, not two.** `ai.sandbox` is the single cross-tool boolean deciding
whether the AI runtime launches inside its own filesystem sandbox;
`resolve_sandbox()` reads CLI `--sandbox`/`--no-sandbox` > `ai.<command>.sandbox`
> `ai.sandbox` > `DEFAULT_SANDBOX` (**`False` — unrestricted**). There is no
per-command asymmetry: that asymmetry is exactly what the retired
`ai.network_access` pin encoded, and what broke delegated multi-model sessions
(every `wade` command is a child of the runtime, so a sandboxed outer runtime
strips a separately authenticated reviewer of its host login).

Network access is now a **constant**, not a setting: `LAUNCH_NETWORK_ACCESS` is
passed `True` at all nine adapter call sites. It must be *passed*, not omitted —
crossby's `network_access` parameter defaults to `False`, so a sandboxed launch
that dropped it would pin `sandbox_workspace_write.network_access=false` and take
the network away from the very lifecycle (`git fetch`/`push`, `gh`) that requires
it. Passing it explicitly also keeps the pin defensive against an ambient
`network_access = true` in the user's Codex `config.toml`. Under `sandbox=False`
crossby ignores the value entirely.

Only tools with `supports_sandbox_toggle` (Codex, Cursor) act on the profile:
Codex maps it to `--sandbox danger-full-access` / `workspace-write`, Cursor to
`--sandbox disabled` / `enabled`. Every other tool receives nothing — never an
invented flag. Resume re-resolves the profile fresh from current config (a
launch-time OS concern, not persisted session state).

**Capability handling is asymmetric by design**, because the default is `False`.
`enforce_sandbox_capability()` is a silent no-op for an unrestricted profile on a
tool that never sandboxed anything, warns when a tool that *does* sandbox writes
has no toggle (unreachable with crossby >= 0.29; the forward-compat guard against
a silent fallback to sandboxed), and raises `SandboxCapabilityError` when
`sandbox: true` is explicitly requested on a toggle-less tool. Because the
terminal default is `False`, a resolved `True` is *always* an explicit request.

Enforcement is deliberately **separate from resolution**. Services resolve the
value early — `bootstrap_worktree` needs it for the guard hooks — and call
`enforce_sandbox_capability()` at the launch site, after `confirm_ai_selection()`
(whose menu can still change the tool) and after the non-launching exits
(`implement --cd`, the nested-AI-session guard) have returned. Checking earlier
would either fail a command that never starts a runtime, or bind the check to a
tool the user then replaced. `resolve_sandbox(tool=...)` is the resolve-and-check
shorthand for the remaining call sites where resolution immediately precedes the
launch.

**The profile changes the external runtime boundary only.** It is orthogonal to
`permission_mode` — Codex's `--sandbox danger-full-access` is a distinct flag from
`--dangerously-bypass-approvals-and-sandbox`, so unrestricted never implies
`yolo` and never alters the resolved autonomy tier. It also never relaxes WADE's
own containment: see the guard-hook note below.

**`sandboxes_writes` is static but the boundary is launch-dependent.**
`_install_guard_hooks` (`implementation_service/bootstrap.py`) narrows the
worktree-containment matcher to the shell token only when
`caps.sandboxes_writes and sandbox` — the capability describes what a tool *can*
do, not what this launch *will* do. Under `sandbox=False` the runtime starts with
`--sandbox danger-full-access` and the **full** matcher is installed, because
nothing else covers tool-call writes outside the worktree. The plan-artifact
guard, worktree metadata grants, lifecycle hooks, Stop hook, and pre-push
backstop are identical in both profiles. `bootstrap_worktree(sandbox=...)`
defaults to `DEFAULT_SANDBOX`, so an omitted argument fails safe to the wide
guard.

The **explicit** `--sandbox`/`--no-sandbox` override (the tri-state
`bool | None`, not the resolved bool) is threaded through every session **handoff**
so it survives instead of the next session silently re-resolving it: the
post-implementation "Wait for reviews" path forwards it via
`_post_implementation_lifecycle` → `review_service.start`, and the numeric
`wade <N>` shorthand forwards it via `SmartStartContext` to whichever route it
picks (implement / batch / review). `_build_implement_cmd` keeps the same
explicit-only rule — an unset value emits no flag, so a child re-resolves from
its own config rather than freezing the parent's resolved value. `None` (unset)
matches how a non-explicit `permission_mode` is threaded.

`requires_fresh_runtime` (`implementation_service/core.py`) is the restated form
of the retired network-pin predicate. It fires only when an accepted plan handoff
runs inside a **detected, known-sandboxed parent runtime** while implementation
resolves **unrestricted**. The planner child has already exited when the accepted
handoff runs, so its requested `--sandbox`/`--no-sandbox` profile is not evidence
about the process now enclosing implementation. Note the **polarity flip** — the
old flag fired on the permissive value being `True` (network on), this one fires
on the permissive value being `False` (sandbox off). A token-level rename would
have silently inverted the intent. A new terminal launched by the confined
process is still only its child — neither terminal-broker acceptance nor a live
readiness probe proves it escaped the OS sandbox — so the handoff fails closed,
names the actual parent runtime, and prints `wade implement <issue>
--no-sandbox`; it must not launch an inline, nested, or detached child. An
unknown parent keeps the ordinary nested-agent guard rather than guessing.

The two tool-identity conjuncts it used to carry — parent is Codex, target is
Codex — were **proxies** for "this process is confined", and they under-fired on
exactly the cases that matter (#480): a sandboxed Codex parent handing off to
Claude confines it just as surely, and being separately authenticated is where
the lost host login shows first. The predicate is now the shared
`requires_unsandboxed_relaunch()` described next.

### Parent-runtime detection & conservative classification (#480)

Every `wade` command started from inside an AI CLI session is a **child** of that
session. If the parent launched under an OS sandbox, every descendant inherits
the boundary: no profile wade resolves, and no flag it passes to a child runtime,
widens it. **An inner wade process can never escape an existing parent sandbox** —
the only fix is relaunching the *outer* session. Code must never imply otherwise.

`utils/runtime_env.py` is the leaf module holding that knowledge, so services,
review, and delegation paths share one probe. It answers two deliberately
independent questions:

| Question | API | Note |
|---|---|---|
| Which session are we inside? | `detect_ai_cli_env()` | The historical identity probe moved out of `core.py` (re-exported there as `_detect_ai_cli_env`, which `__all__` and much of the suite still patch). Drives the nested-launch guard. |
| Is that session confined? | `assess_parent_sandbox()` → `SandboxAssessment` | Tri-state: `SANDBOXED` / `UNRESTRICTED` / `UNKNOWN`. |

Keeping them independent matters in both directions: identity alone says nothing
about confinement (Codex runs either way under the canonical `CODEX_CLI`
identity, including managed `CODEX_SESSION_ID` / `CODEX_THREAD_ID` aliases), and
a sandbox signal is actionable even when the tool cannot be named.

**`UNKNOWN` is a first-class answer, never a fallback to a guess.** The
assessment is read only from published signals: `CODEX_SANDBOX` (high confidence
— Codex exports it only when a policy applies and names that policy; a value
naming an explicitly unconfined mode reads `UNRESTRICTED`, any other non-empty
value reads `SANDBOXED`, absent reads `UNKNOWN` because an older Codex may not
export it) and `CODEX_SANDBOX_NETWORK_DISABLED` as a secondary. Every other
runtime publishes nothing, so `UNKNOWN` is correct and final for them — crossby's
static `sandboxes_writes` capability describes the *tool*, not this process's
boundary, and inferring from tool identity is exactly the confident wrong cause
the module exists to avoid. A requested profile of a finished child process is
not a substitute for an OS signal on the runtime that currently encloses wade.

**One predicate, four call sites.** `requires_unsandboxed_relaunch()` is true
only for *resolved profile unrestricted* **and** *parent known sandboxed* — a
published signal remains enough to warn even if the separate identity probe has
no match. Identity is still required only for the nested-launch guard and the
plan-handoff fail-closed branch.
`ai_resolution.announce_inherited_sandbox()` is the single user-facing emitter, so
the paths cannot drift into four explanations of one boundary. It is **advisory,
never a block** (wade cannot prove the delegated tool will fail) and says nothing
at all on `UNKNOWN`. Host-terminal recovery commands are built as quoted argv,
then displayed literally, preserving each operation's supported resolved tool,
model, effort, permission mode, and methodology bindings (plus review mode and
staged scope where applicable); only the sandbox profile changes to
`--no-sandbox`.

| Launch path | Site | Relaunch command it supplies |
|---|---|---|
| implementation | `implementation_service/core.py` | `wade implement <id> --no-sandbox` |
| PR-comment review | `review_service.py` | `wade review pr-comments <id> --no-sandbox` |
| plan | `plan_service.py` | `wade plan --issue <id> --no-sandbox` (or `wade plan --no-sandbox`) — built here because it calls `build_launch_command` + `run_with_transcript` directly, bypassing `delegate()`, and has no nested-AI guard |
| deps, standalone plan/code review, batch review | `delegation_service.delegate()` | per-operation, from `DelegationRequest.operation` / `.relaunch_command` |

Deps (`deps_service.py`), standalone review (`review_delegation_service.py`) and
batch review (via `review_delegation_service`) all funnel through the one
`delegate()` dispatcher, so this cross-cutting concern lives there **once**.
Centralising costs per-operation context, which is why the remediation fields
ride on `DelegationRequest` — generic advice would defeat the purpose. The
dispatcher emits that advisory only after tool/mode validation and immediately
before a runtime launch; a missing tool or headless-capability refusal cannot be
fixed by relaunching the enclosing session. The command is built by
`review_delegation_service._relaunch_command()`, which
forces `--no-sandbox` (so the host-terminal retry overrides any sandboxed config)
and appends the operation's own **required positional operand** (the plan path
for `wade review plan`, the tracking issue for `wade review batch`). It withholds
the hint entirely when it has no required operand: those base commands are
rejected by Typer, and a line the user cannot paste is worse than no line — the
same rule already applied to an unmapped command.
`batch.py` deliberately has **no** check: it spawns child `wade implement`
processes that reach the `core.py` check themselves, so one here would be a dead
no-op implying coverage it does not add (pinned as source text in
`test_batch_build_cmd.py`, since no behavioural assertion can distinguish "no
check" from "a check that happens not to fire").

**`DelegationResult.never_launched`** closes the gap that used to force hedged
remediation: it is `True` only when no process ever started — an absent/unknown
tool, a `supports_headless` rejection, a `CommandError` spawn failure, or an
`OSError` on exec (`utils.process.run` maps only `FileNotFoundError` to
`CommandError`, so a `PermissionError` on a resolvable-but-unexecutable binary
used to escape `_delegate_headless` and abort the command with a traceback). A
**non-zero exit** keeps it `False`: that process ran, and a timeout still sets
`timed_out` and still consumes a pass. The interactive path splits the launch
boundary into its own handler and classifies by **exception shape**, because
`adapter.launch` blocks until the child exits for most runtimes: an `OSError` at
the spawn started nothing, while every `subprocess.SubprocessError` variant
(`CalledProcessError`, `TimeoutExpired`) carries a child that was created and so
counts as launched even though the call never returned. Everything after the
adapter returns — the confirm prompt, the output-file read — is post-launch by
position, whatever it raises. The headless path matters most — this repo configures
`mode: headless` for `deps`, `review_plan` and `review_implementation`, and the
receipt gate excludes `PROMPT` outright.

Failure text is matched against sandbox-denial *shapes*
(`looks_like_sandbox_denial`) — permission/exec denials, read-only filesystems,
network denials. Those generic OS errors stay **hedged** even when the parent is
known sandboxed: non-executable binaries and local network configuration can
produce the same text. Only explicit policy markers (`sandbox policy`, `seatbelt`,
or `landlock`) support a confident sandbox cause. "Command not found" is
deliberately **excluded**: from inside a sandbox the host filesystem is exactly
what is not observable, so "a binary that exists on the host but is missing here"
is unverifiable at the point of failure and equally the signature of a genuinely
uninstalled tool.

**Review-gate outcome semantics.** `ReviewOutcome.UNATTEMPTED`
(`models/session_manifest.py`) has `satisfies_review` **and** `consumes_pass`
both `False`, and sits at the **bottom** of `_OUTCOME_PRECEDENCE`
(`review_record_service.py`) — a reviewer that never started carries strictly
less information than any outcome from one that did, so recording it can never
downgrade or overwrite a real receipt for the same commit+binding. Budget
protection (#462) and receipt *classification* remain separate problems: the gate
at `review_delegation_service.py` already withheld budget, and `never_launched`
is what now lets the remediation be trusted instead of hedged.
`_report_failed_review()` grades it by what wade actually knows — (1) known
sandboxed parent **and** an explicit policy denial → state the cause and exact
relaunch command; (2) a generic denial-shaped failure → explain it might also be
executable permissions or network configuration alongside `_HEDGED_REVIEW_FAILURE`;
(3) anything else → that hedged wording alone, kept verbatim. `done` gates stay
closed in every case.

The denial shape is required in (1), not implied by the sandbox: a known boundary
says the reviewer *could* have been denied, never that it *was*. Without it every
`never_launched` result took that branch — including a configuration refusal that
never touched the OS (`Unknown AI tool`, `No AI tool specified`) and a genuinely
uninstalled binary — blaming inaccessible host credentials and returning before
the more useful generic remediation. `UNATTEMPTED` is still recorded from
`never_launched` alone; only the *explanation* is graded.

**Retirement path for `ai.network_access`.** The loader deliberately never reads
the key (an un-migrated `.wade.yml` keeps loading), `strip_retired_network_access`
removes it globally and per command on the next migration, and `check_service`
carries an explicit `_DEPRECATED_AI_KEYS` tolerance. That tolerance is
load-bearing: `_VALID_AI_COMMAND_KEYS` / `_VALID_AI_TOP_LEVEL_KEYS` are derived
reflectively from the Pydantic models (#368), so deleting a field silently drops
its key from the allowlists and the generic `unsupported key` loop turns an
un-migrated config into a **hard error**, independent of any type validator. Any
future key retirement needs the same tolerance entry.

### Phase-aware session readiness (#462)

`check_service.check_session_readiness()` applies the declarative
`READINESS_REQUIREMENTS` model to the **agent child runtime** for `plan`,
`implementation`, `review-pr-comments`, and `deps`. This is deliberately not a
description of the parent `wade` process: a parent can call a task provider once
an offline detached agent exits, while a `wade` command issued *by* an agent
inherits its tool's filesystem sandbox, environment/PATH, command approvals,
network policy, credentials, and hook environment.

The stable result states preserve the original `IN_WORKTREE` / main / non-repo
contracts and add `GITHUB_CLI_BLOCKED` (4), `GITHUB_AUTH_BLOCKED` (5),
`GITHUB_API_BLOCKED` (6), `KNOWLEDGE_STAGING_BLOCKED` (7), and
`PLAN_DIR_BLOCKED` (8); each names a
`ReadinessFailure` such as `github_cli_executable` or
`github_api_reachability`. One further state, `PLAN_DIR_ONLY`, is added as a
**success** (exit 0, no `ReadinessFailure`) — see *Plan-directory fallback*
below. GitHub readiness first runs local `gh --version` so
a missing or exec-blocked CLI is not misreported as failed authentication, then
runs `gh auth status --active` and `gh api user --method GET`; none mutate a
remote. `--active` is deliberate — plain `gh auth status` exits 1 when *any*
account known for the host is unhealthy, so one stale secondary login would
block every session even though `gh` operations use the healthy active account.
An older `gh` that rejects the flag falls back to the unfiltered probe rather
than turning a working login into a hard block. Both probes pass the same
explicit hostname: `GH_HOST` when set, otherwise the hostname parsed from the
repository's `origin` (HTTPS/SSH and scp-like SSH remotes). This keeps a GitHub
Enterprise worktree from accidentally probing github.com, which is `gh api`'s
default when no hostname is given.

**Plan-directory fallback.** `plan()` keeps a supported worktree-less mode: if
the detached planning worktree cannot be created (bootstrap failure, or no git
repo at all), it writes plan files to a throwaway directory and launches the
agent from the caller's checkout. A bare worktree check would report
`IN_MAIN_CHECKOUT` / `NOT_IN_GIT_REPO` there and the mandatory first action
would tell the agent to stop, making the fallback unusable. `plan()` therefore
exports `WADE_PLAN_DIR` (`PLAN_DIR_ENV_VAR`) for the duration of that launch
only; PLAN readiness consults it exclusively when the base check did **not**
return `IN_WORKTREE`, write-probes the directory, and returns `PLAN_DIR_ONLY`
(exit 0, `plandir=…`) or `PLAN_DIR_BLOCKED` (8, `plan_output_write`). Because
only the parent sets the marker, a genuinely misplaced agent still gets
`IN_MAIN_CHECKOUT`, and a real worktree-mode failure (e.g. blocked staging) is
never masked. Plan review resolves the frozen REVIEW binding from that throwaway
directory but launches the reviewer in the caller's source checkout, granting
the throwaway bundle as an additional read root. Binding identity and repository
context therefore remain separate without hiding the source tree from the
review methodology.
They apply to implementation and PR-comment sessions **regardless of the task
provider**, because PRs, review threads, and `done` are GitHub-backed even when
tasks are Markdown or ClickUp. Those two phases also require writable linked-worktree Git metadata:
they execute fetch/sync/done. Detached planning and dependency analysis require
only their local output area plus (when knowledge is enabled) a self-cleaning
probe of `.wade/` vote staging; they intentionally do not require GitHub,
network, or writable out-of-root Git metadata because the trusted parent
performs provider finalization after the child exits.

The session CLIs expose `plan-session check`, `deps-session check`,
`implementation-session check`, and `review-pr-comments-session check`. The
templates require the first check in the actual AI runtime and a re-check before
implementation/review `sync` or `done` after a resume or capability change. The
check cannot prove access to an arbitrary Git remote, project test dependencies,
or a project-specific hook: those later subprocesses still inherit the same
sandbox and report their own failure. Tool command allowlists authorize an agent
to start `wade`; they do not grant that child new filesystem/network authority,
and hooks are guardrails rather than privilege escalation.

The implementation-review pass cap distinguishes an attempted, bounded review
from a reviewer that never launched: success and a true headless timeout count
toward `done.max_review_passes`, but missing credentials/PATH or a sandbox exec
denial do not. Otherwise an unavailable reviewer could exhaust the cap and let
`done` bypass its review gate without any review attempt.

The capability remediation is intentionally tool-neutral: retain the sandbox
and grant only the worktree Git metadata paths, GitHub credential/API route, or
local staging path named by the failure. Codex uses the resolved `ai.sandbox`
profile and, when sandboxed, additive worktree metadata grants; Claude/Cursor use
path/domain allowlists; Copilot/VS Code need host-networked `gh` credentials;
OpenCode has host-authority shells and must be launched only in a trusted host
context. Wade never rewrites those external tool settings or grants the main
checkout write access as a readiness workaround.

### Session-start context injection (#351)

The launch prompt injects the task **once**. Nothing re-injects it on **resume**
or after **compaction** — and compaction is the largest single context loss.
`bootstrap_worktree` therefore installs a **SessionStart** hook (`wade-hook
session_start`) that re-injects a compact, phase-gated reminder as
`additionalContext` on every session-start source. It is **non-blocking** (like
the Stop hook, no `fail_closed`) and **fail-open**: a missing `--root`/`--phase`,
an unreadable `PLAN.md`, or any exception yields exit 0, so it can never trap a
session from starting.

- **Policy** (`hooks/policies.py::session_start_context`): assembles compact text
  by the hook-specific `SessionPhase` (`implement` / `review` / `plan`). The
  AI-facing prose lives in `templates/prompts/session-start-<phase>.md` and points
  back to the frozen `.wade/session/WORKFLOW.md`; it never duplicates
  configuration-specific review policy or methodology text. For impl/review the
  builder may prepend the issue ref parsed from `PLAN.md`; for plan it may prepend
  the ref from `.wade/plan-issue.md`. It remains import-light/stdout-safe and is
  capped at **≤ 800 characters**.
- **Install** (`bootstrap.py::_install_session_start_hook`): gated on
  `supports_session_start_hook` (mirroring how the Stop hook gates on
  `supports_stop_hook`). `tools=[]` is **load-bearing** — `_tools_to_matcher([])`
  → `.*`, and the SessionStart matcher is tested against the *source*, so `.*`
  re-fires on `startup`/`resume`/`compact`/`clear`/`fork`. Narrowing it would
  silently drop compaction re-injection. Because crossby dedups hooks by exact
  command, a **reused** worktree (impl → review re-bootstraps with a different
  `--phase`) would otherwise fire *both* phase reminders; the install revokes every
  other-phase variant via `hooks_remove`, leaving exactly one entry.
- **Sessions**: installed for implementation, review, and plan sessions (each
  passes a `session_phase` to `bootstrap_worktree`). `wade task deps` passes
  `None` and opts out (short, detached, no completion-gate decay). `plan_mode` and
  `session_phase` stay independent signals — the invariant `plan_mode is True` iff
  `session_phase is SessionPhase.PLAN` is pinned by a test, not derived in code.

Per-tool payload shape (all delegated to crossby's `emit_decision`; **no crossby
change or pin bump** was needed — as of crossby 0.17 it already serializes
`action="context"` for every session-start dialect):

| Tool | Event | Payload key |
|------|-------|-------------|
| Claude, Codex | `SessionStart` | nested `hookSpecificOutput.additionalContext` |
| Copilot | `sessionStart` | flat `additionalContext` |
| Cursor | `sessionStart` | `additional_context` (gated to the events Cursor reads it on) |
| Antigravity CLI (`agy`) | — | no hook installed (`DECISION` dialect has no context channel); **degrades to the launch-loaded workflow** |

**Deferred (evaluated, not built):** `SessionStart.initialUserMessage` as a
stronger resume carrier — crossby's `emit_decision` has no such channel, so it
would need a crossby change; `additionalContext` covers resume *and* compaction
uniformly and is the verified channel. `UserPromptSubmit` per-turn injection —
the wiring exists but injecting on every prompt conflicts with the "compact,
say-it-once, low-cost" goal.

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

1. **conventional title** (both) — reject a task title that would create a
   failing PR title.
2. **PR-SUMMARY** (both) — present, non-empty, and non-placeholder.
3. **unresolved threads** (review-pr-comments only) — a transient provider error
   remains non-blocking.
4. **review-ran** (both) — for a versioned session, require a satisfying
   structured record for the pre-sync HEAD and the manifest's REVIEW composite.
   Before reading records or counting passes, recompute and validate the frozen
   workflow/skill bundle against the manifest; invalid content is not trusted and
   cannot reach the exact-review or pass-cap success paths.
   `reviewed` and deterministically empty `no-diff` satisfy the gate; timeout and
   empty-staged-with-other-work do not. Implementation sessions cap only
   pass-consuming records for that active reviewer. PR-comment review remains
   uncapped. A successful record under a different reviewer produces the explicit
   `REVIEWER_CHANGED` classification. SHA marker files are never accepted as
   review evidence.
5. **documentation decision** (both) — require an `updated` or reasoned
   `not-needed` receipt for the pre-sync HEAD and session kind.
6. **sync** (both) — auto-sync through the existing service and refuse only on
   conflict.
7. **knowledge validity** (both, when enabled) — runs after sync so a union-merge
   corruption cannot ship.
8. `_done_via_pr` writes `.wade/done@<post-sync HEAD>` immediately before pushing
   and projects the review outcome into the PR body.

**Review status is legible in the PR body (#367).** `_classify_review()` is the
single source for the gate and the `wade:review-status` PR-body block. It records
reviewed-at-`<sha>`, reviewer changed, skipped, gate disabled, or cap reached and
includes the active binding's pass count. The structured worktree records under
`.wade/reviews/` provide deterministic gate evidence; the PR-body projection is
the durable human-readable outcome.

Binding-aware review records and documentation receipts use the descriptor-safe
state primitives in `utils/safe_state.py`: `.wade` and intermediate directories
must be real directories, final files are opened relative to trusted directory
handles, and malformed/symlinked/unreadable state is treated as absent. Record
filenames and bodies must agree, and every binding digest is recomputed from its
ordered components. See [Workflows and Dynamic Skills](workflows-and-skills.md).

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
`git/hooks.py:install_worktree_git_hooks`) makes the gate hard to skip: git
runs it with cwd at the worktree top, so it tests `[[ -f ".wade/done@${sha}" ]]`
in pure shell. It is wired per-worktree via `extensions.worktreeConfig` +
`git config --worktree core.hooksPath .wade/githooks` (git ≥ 2.20; **graceful
degrade** to warn-and-skip otherwise), so it never leaks to the main checkout or
sibling worktrees. Because `core.hooksPath` *replaces* `.git/hooks`, `git/hooks.py`
detects any pre-existing hook once at first install, persisting it per-hook to
`.wade/githooks/.chain-<hook_name>` (so `pre-push`/`pre-commit`/`commit-msg` each
chain to their own captured prior), and the wade hook **chains** to it (re-emitting
the exact buffered stdin) rather than silently shadowing it. The same
`install_worktree_git_hooks` batch API installs all of `pre-push`, `pre-commit`,
and `commit-msg` in one call so their chaining stays correct. **Honesty:**
`git push --no-verify` bypasses it in one flag — a quality/backstop layer, not a
boundary.

## Command Dispatch

`src/wade/cli/main.py` is the root Typer application. It registers subcommand groups (`task`, `worktree`, `plan-session`, `deps-session`, `implementation-session`, `review-pr-comments-session`, `review`, `knowledge`) and admin commands (`init`, `update`, `deinit`, `check-config`, `shell-init`). The `tasks` alias (for `task`) and `address-reviews-session` (for `review-pr-comments-session`) are hidden Typer groups pointing at the same apps, and `hook` is a hidden write-guard entry point invoked by AI tools. The `wade` entry point (defined in `pyproject.toml` as `wade.cli.main:cli_main`) invokes the root app.

CLI modules are **thin dispatch layers** — they parse flags via Typer, then call service methods. Business logic lives in `services/`, not in `cli/`.

**Interactive menus**: `wade task` and `wade worktree` with no subcommand show interactive menus. `wade task create` prompts interactively for title and body. Top-level commands `plan`, `implement`, `implement-batch`, and `cd` are registered directly on the root app (alongside the hidden `smart-start` and `address-reviews` commands). The `review` subcommand group provides `plan`, `implementation`, `pr-comments`, `trigger`, and `batch` commands. Hidden short aliases `p`, `i`, and `r` map to `plan`, `implement`, and `review pr-comments` respectively. The numeric shorthand `wade <N>` is rewritten to the hidden `smart-start` command in `cli_main()`, which detects PR state and routes to implement or review pr-comments.

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
models:                      # values below are crossby's claude defaults, not wade constants
  claude:
    easy: claude-haiku-4.5
    medium: claude-sonnet-5
    complex: claude-sonnet-5
    very_complex: claude-opus-5
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
skills:                      # optional project-skill discovery policy
  project:
    discover: true
    include: ["*"]
    exclude: []
sessions:                    # optional ordered interactive bindings
  plan:
    skills:
      work: [builtin:planning]
      review: [builtin:plan-review]
  implementation:
    skills:
      work: [project:domain-implementation]
      review: [project:security-review]
  review_pr_comments:
    skills:
      work: [builtin:review-comments]
delegations:                 # optional standalone/fallback bindings
  plan_review:
    skills:
      work: [builtin:plan-review]
  code_review:
    skills:
      work: [project:security-review]
  batch_review:
    skills:
      work: [builtin:batch-review]
  dependency_analysis:
    skills:
      work: [builtin:dependency-analysis]
done:                        # completion-gate toggles (all default true)
  require_pr_summary: true
  require_sync: true
  require_review: true
  require_resolved_threads: true
  require_conventional_title: true  # block a non-conventional issue title; sync it onto the PR (#392)
  pre_push_backstop: true
  max_review_passes: 2       # impl-session review→fix loop cap (#384); strict positive int
bot_review:                  # external-bot review triggers (#431); fully defaulted
  auto_trigger: false        # opt-in; when true, `done` posts triggers after it pushes
  offer_on_done: true        # #464: when auto_trigger is off, `done`/menus OFFER the triggers
  arrival_timeout: 300       # #448: seconds to wait for an enabled bot to review HEAD
  ack_timeout: 900           # #448: longer ceiling once a bot reacts (👀/+1); must be >= arrival
  bots:
    - { name: coderabbit, trigger: "@coderabbitai review", enabled: true }
    - { name: codex,      trigger: "@codex review",        enabled: true }
    - { name: bugbot,     trigger: "bugbot run",           enabled: true }
```

**Dynamic skill sections** (`SkillsConfig`, `SessionsConfig`,
`DelegationsConfig`) are optional additions to schema version 2. Nested models
forbid unknown keys and bindings must be non-empty ordered lists of explicit
`builtin:`, `project:`, or normalized repository-relative `path:` refs. `deps`
is intentionally absent from `sessions`: its AI method is configured only as
`delegations.dependency_analysis`. The hand-written loader, `check-config`, and
re-init raw-YAML preservation all cover these sections. Fresh init omits them by
default. See [Workflows and Dynamic Skills](workflows-and-skills.md) for
precedence and frozen resume semantics.

**`done` section** (`DoneConfig`): completion-gate escape hatches, all default
on. Per knowledge `ca245d6a`, config-key validity lives in **three** places that
can drift — the Pydantic model (`models/config.py`), the loader
(`config/loader.py`), and the `check_service.py` validator. The `done` validator
allowlist is **derived** from `DoneConfig.model_fields`, so a new field is
accepted automatically.

**`bot_review` section** (`BotReviewConfig` / `ReviewBotConfig`, #431): a
**top-level** section (deliberately *not* under `ai.review_*`, which configure
wade's own AI-tool reviews — these are external-bot trigger strings posted as PR
comments). Fully model-defaulted: an absent section yields `auto_trigger: false`
and the three built-in bots (CodeRabbit / Codex / Bugbot), produced by
`Field(default_factory=_default_review_bots)` so no list instance is shared
across `ProjectConfig`s. Because `_build_config` is hand-rolled per section, the
model alone is not parsed — `_parse_bot_review` in `config/loader.py` is the
explicit parse block (a present section overrides `auto_trigger` /
`offer_on_done`; an explicit `bots` list replaces the defaults wholesale, an
omitted one keeps them). Bot
`name` values are **model invariants** (`ReviewBotConfig`/`BotReviewConfig`
validators, enforced on every construction path — not only `check-config`): they
must be **unique** and a **safe identifier** (`[A-Za-z0-9._-]+`), since `--bot`
selection and the per-bot auto-trigger marker (a `.wade/` filename component)
both key off `name`. `check_service` mirrors both rules for friendly
`check-config` messages, and its `bot_review` key allowlist is **derived** from
`BotReviewConfig.model_fields` (like the `done` validator above), so a new field
is accepted without a hand-edit. No config-version migration is needed. `wade review trigger <issue>`
(`review_service.trigger_bot_reviews`) posts the enabled bots' triggers via
`git_pr.comment_on_pr`, wrapping **each** post in its own try/except so one
failing bot doesn't abort the rest, and returns a `BotTriggerReport`
(`models/review.py`) whose `exit_code` the CLI uses. The manual command never
reads or writes the per-SHA markers described below, so a same-SHA automatic
trigger still fires. Untrusted text interpolated into markup-enabled console
output — provider/exception error text, and arbitrary `--bot` values — is escaped
(`console.escape_markup`) so a stray Rich control token can't raise `MarkupError`
(even on `--dry-run`).

**Marker-aware triggering** (`services/bot_trigger.py`, #431/#464): every surface
that triggers as a *side effect of finishing a session* shares this leafish
service module — `pending_bots`/`pending_names` (enabled bots with no
`.wade/bot-triggered-<name>@<sha>` marker), `post_bot_triggers` (post + record),
`menu_entry`/`post_pending_triggers` (the post-session menu pair). Triggers fire
**at most once per bot per commit SHA** (`utils/markers.py:write_marker`, written
only after that bot's post succeeds — so a failed bot retries, a succeeded one
never re-posts). `post_bot_triggers` runs the whole check→post→record section
under a cross-process `utils/filelock.file_lock` keyed on worktree+SHA, and
re-checks each bot's marker *inside* the lock — so two concurrent `done`/menu
processes on one worktree can't both read a marker absent and double-post
(the guarantee holds under process-level parallelism, not just sequential
re-runs); if the lock primitive is unavailable it degrades to the unlocked
best-effort check-then-act rather than fail an otherwise-complete `done`. A
failed marker *write* is warned rather than reported as durable success, since
the comment is already posted but a later same-SHA pass may re-post it. (The `name` safe-identifier invariant means a `/` can no longer break the
marker path, so a `False` write now signals a genuine I/O failure.) An
unresolvable branch sha means the markers cannot dedupe, so every caller declines
to post rather than risk a comment per pass.

Consumers, all fed from that one module:

- `implementation_service.done()` → `_done_via_pr` → `_maybe_trigger_bot_reviews`
  (covers **both** session `done` commands). Resolution order: the
  `--trigger-bots` / `--no-trigger-bots` flag (`trigger_bots: bool | None`,
  threaded CLI → `done()` → `_done_via_pr`) > `auto_trigger` > `offer_on_done` >
  silence. The offer is a `prompts.confirm` on a TTY and a printed
  offer-in-your-closing-dialog line otherwise — `done` normally runs in a
  non-TTY AI session, so the agent, not wade, is the one who can ask the human.
  This offer runs *after* the push and PR finalize, so a Ctrl+C at the confirm is
  treated as a declined offer (`prompts.confirm(cancel_default=False)`) rather
  than aborting `done` and skipping its worktree cleanup for an already-live PR.
- `implementation_service.lifecycle._post_implementation_lifecycle_pr` and
  `review_service._post_review_lifecycle` / `_quiet_next_steps_prompt` — each
  **appends** (never inserts) a `Trigger bot reviews (…)` entry, so existing
  choice indexes are untouched; `menu_entry` returns `(None, None)` and the menu
  renders unchanged whenever resolution fails, since an offer must never break
  the merge/wait menu it decorates. Picking the entry only falls through into the
  wait-for-review poll when `post_pending_triggers` reports a review is now
  pending (`bool`: a trigger posted, or every bot already recorded for this SHA);
  when every post fails (e.g. a GitHub outage) it returns instead of silently
  waiting for a review no bot was asked for.

**Expectation-verified review completion (#448)**: `arrival_timeout` / `ack_timeout`
turn review completion from *presence-inferred* (no blocking signal → done) into
*expectation-verified* — WADE refuses to report all-clear while any **enabled** bot
has not posted a review covering HEAD. The mechanism is split by layer: the
**provider** (`github.py`) fetches raw PR-level bot reactions (verified real signal:
Codex posts a PR-level `THUMBS_UP`) into `PRReviewStatus.bot_reactions` via the same
combined GraphQL page (no extra REST call); the **model** (`models/review.py`) holds
the pure `compute_bot_arrivals()` helper (returns a per-bot
`dict[str, BotArrival]` — `ARRIVED`/`AWAITING`/`ACKNOWLEDGED`/`MISSING` — and the
`bot_login_matches()` name→login matcher, verified logins `coderabbitai[bot]` /
`chatgpt-codex-connector`, best-effort `cursor`/`bugbot`), and `review_covers_latest_commit`
gates on `blocking_bots` when `expected_bots` is set; the **service**
(`review_service.annotate_bot_expectations`) is the only layer with both config and
a runtime clock, so it populates `expected_bots` from the enabled bots and computes
the arrival map (arrival window measured from the later of the commit push and a
`.wade/bot-triggered-<name>@<sha>` marker, falling back to the commit push when
absent). `poll_for_reviews` accepts `marker_root` separately from the git/provider
`repo_root`; menu-triggered polls pass the linked worktree here so a fresh trigger
marker resets the arrival window even when provider operations run from the main
checkout. The arrival-window comparison is a service/param concern — the model
stays config-free (layering rule). Every completion surface reads the map: the poll
loop (`poll_for_reviews`, gated on `config`), single-shot `start()`, the
AI-agent-facing `fetch_reviews()`, and `format_review_status_summary`. When
`expected_bots` is empty (no config passed, or all bots disabled) the model behaves
exactly as before #448. See knowledge `cc91cd11` for the generalized principle.

**Model complexity mapping**: The `models` section maps AI tool names to complexity-tiered model IDs (`easy`, `medium`, `complex`, `very_complex`). When `wade implement` is invoked, the service reads the `complexity:X` label from the issue (falling back to `## Complexity` in the body), maps it to the appropriate configured model, and passes it as `--model` to the AI tool — unless the user explicitly passed `--model` themselves.

The tier values `wade init` writes are **not wade constants** — `init_service/config_io.py::_resolve_models()` seeds them from `crossby.config.defaults.get_defaults(tool)`, so **bumping the crossby pin changes what newly initialized projects get**. The example above shows crossby's `claude` defaults at the currently pinned version; run `get_defaults()` for the authoritative set rather than trusting a doc snapshot, and re-check this block on every crossby bump. A bump does **not** rewrite tiers an existing `.wade.yml` already sets: `wade update` re-resolves the defaults and calls `_patch_config` without `force`, which backfills only *absent* tiers. So a fresh `wade init` picks up the new defaults wholesale, while an existing project changes only where it left a tier unset.

**Per-command AI tool and model overrides**: The `ai` section supports `plan`, `deps`, `implement`, `review_plan`, `review_implementation`, `review_batch`, and `review_pr_comments` sub-sections (`AI_COMMAND_NAMES` in `models/config.py`), with optional `tool`, `model`, `mode`, `effort`, `enabled`, `yolo`, `permission_mode`, `sandbox`, and `timeout` keys as applicable. `sandbox` (global `ai.sandbox` or per-command) is the cross-tool AI-runtime sandbox profile resolved by `resolve_sandbox()`: CLI `--sandbox`/`--no-sandbox` > per-command > global > **`False` (unrestricted)** for every command, with no per-command asymmetry. Network access is unconditionally on and no longer configurable — see the "AI runtime sandbox profile" section above. `timeout` bounds a headless subprocess (seconds). When **unset**, the review/deps services compute the budget with `effective_timeout` (`delegation_service.py`, #366): it scales from **payload bytes + reasoning effort** — `scaled_timeout` starts at a **600s floor** (`TIMEOUT_FLOOR`, covers CLI cold-start + a small high-effort run), adds ~0.0075 s/byte of prompt, multiplies high/xhigh/max effort by 1.5–1.75×, and clamps to a **1500s ceiling** (`TIMEOUT_CEILING`). A headless timeout is **not** discarded: `run` (`utils/process.py`) decodes and reattaches the partial stdout (bytes even under `text=True`), `_delegate_headless` returns it as `feedback` with `DelegationResult.timed_out=True`, and wade **retries once** at a longer budget (`extended_timeout`, 1.5×) — bounding the *sum* of both legs to `TOTAL_TIMEOUT_CAP` (`TIMEOUT_CEILING + TIMEOUT_CEILING * TIMEOUT_RETRY_MULTIPLIER`, ~3750s / 62.5 min) so the worst case is predictable while the retry always gets the full multiplier, never a shorter budget than the attempt that just timed out (#366 review). The pre-launch advisory — now also printed by `deps_service.analyze_deps` before a headless run, not just the review commands (#366 review) — announces that worst-case total. A crash (`CommandError` / non-zero exit) is never retried and never flagged `timed_out`. Setting `ai.<cmd>.timeout` **explicitly** is a deliberate override: it is honored verbatim and **bypasses scaling and the retry math** — the escape hatch for orchestrators with a hard tool-timeout (set it below the harness limit). The fallback chain (tool/model) is: CLI `--ai`/`--model` flag -> command-specific config -> global `default_tool`. This is implemented in `ProjectConfig.get_ai_tool(command)` and `ProjectConfig.get_model(command)`. When `mode` is omitted, `review_plan` and `review_implementation` default to `prompt`, while `review_batch` defaults to `interactive`. `review_pr_comments` (#389) governs the **auto-launched review session** (post-`done` "Wait for reviews" → comments land → `review_service.start`): it resolves that session's tool, model, effort, and autonomy tier under its own key rather than inheriting `ai.implement.*`. The inherited implementation-session `tool` / `model` / `permission_mode` are honored only when the user set them *explicitly* (`--ai` / `--model` / `--permission-mode` / `--yolo`); the implementation flow forwards its already-*resolved* concrete values (never `None`), which would otherwise short-circuit the resolvers and shadow `ai.review_pr_comments` — so a merely config/default-derived value is dropped and the review config (then global `ai.*`) governs.

**Permission (autonomy) mode vs. delegation `mode` — two orthogonal axes**: The `mode` key (`DelegationMode`: `prompt`/`interactive`/`headless`, `models/delegation.py`) governs *how* a tool is dispatched. `permission_mode` (`PermissionMode`: `default`/`accept-edits`/`auto`/`yolo`, `models/permission.py`) governs *how much* the tool may do without prompting — the autonomy axis crossby exposes via the `yolo`/`auto`/`accept_edits` launch booleans. Do **not** conflate them: they live in separate modules on purpose. Resolution (`resolve_permission_mode()` in `ai_resolution.py`) follows CLI `--permission-mode` > `--yolo` alias > command config > global config > `default`; `permission_mode` wins over the legacy `yolo` alias at any level, and `get_yolo()`/`resolve_yolo()` are thin shims that derive from the resolved mode so the alias has a single source of truth. WADE forwards only the *requested* tier and does **not** gate on per-tool capability — crossby owns capability-aware downgrades and warnings (`_autonomy_launch_args`), so `auto` on a non-Claude tool downgrades to `accept-edits` instead of WADE silently disabling it. The headless delegation path always forces `default` (no autonomy grant) regardless of config, since `deps`/`review_plan`/`review_implementation`/`review_batch` are read/analytical; `review_pr_comments` is the exception — it launches an *interactive* session and honors its configured `ai.review_pr_comments.permission_mode`. `plan` is intentionally excluded from `PermissionMode` (WADE drives plan mode separately via `plan_service` → `plan_mode=True` for native plan tools, and `plan_mode=False` for Antigravity CLI whose native plan mode sandboxes writes to an external brain store while WADE's plan-artifact guard enforces containment); a configured or CLI-supplied `permission_mode: plan` (or any invalid value) warns and falls back to `default`. Every launch command (`plan`, `implement`, `implement-batch`, `review pr-comments`, `review plan`/`implementation`/`batch`, `task deps`, and the delegation paths) exposes `--yolo`/`--permission-mode` and resolves + forwards the tier; `confirm_ai_selection()` (`ai_resolution.py`) **always displays** the resolved tool/model/effort/permission mode with a per-tier descriptor (`permission.describe_permission_mode`) before its skip guard, so the mode surfaces on every path (TTY, non-TTY, headless, all-flags-explicit) and what is shown always equals what is applied. For the read-only headless paths (`deps`/`review_*` in headless mode), the service computes the *effective* mode as `default` and uses that single value for both display and the `DelegationRequest`, mirroring the `delegation_service` headless force-default rule. In addition, a completed non-zero headless exit preserves trimmed stdout and appends a clearly labeled stderr tail (the final 20 non-empty lines, capped at 4,000 characters, with truncation labeled); only failures with neither stream retain the generic no-output fallback.

**Worktree hooks**: The `hooks` section lets projects run setup automatically when a worktree is created. `post_worktree_create` points to a script that runs in the new worktree (e.g., installing dependencies). `copy_to_worktree` lists files to copy from the project root into the worktree before the hook runs (e.g., `.env`). Hook failures are non-fatal — a warning is logged and the session continues.

**Repo-quality gates** (`HooksConfig` → `PreCommitConfig` / `CommitMsgConfig` / `PostToolUseConfig`, #352): three opt-in, off-by-default subsections. `pre_commit.{lint,test}` and `commit_msg.conventional` install per-worktree `pre-commit` / `commit-msg` git hooks (baked from config at bootstrap via placeholder substitution — no per-commit config load). They are reconciled together with the `done.pre_push_backstop` `pre-push` hook by `reconcile_worktree_git_hooks`, which installs the desired set in one batch (so every prior user hook is captured **before** wade sets `core.hooksPath` — per-hook `.chain-<hook_name>` files; a #349 unsuffixed `.chain` is migrated to `.chain-pre-push` on upgrade) **and** is idempotent across re-bootstraps of a reused worktree: a gate turned off since a prior session is neutralized (a chain-only passthrough that still runs any captured prior, or a full uninstall + `core.hooksPath` unset when nothing is managed), so disabling a gate actually disables it. `post_tool_use` installs a PostToolUse hook into context-capable tools only (dialect ≠ `DECISION`, so Antigravity CLI is skipped and its prior entry removed); the command is **stable** (`wade-hook post_tool_use --tool <id> --root <root>`) and resolves `lint_cmd`/timeout/scope from `.wade.yml` at runtime, so re-bootstrap dedups, a disabled gate's entry is removed (and a leftover hook self-noops), and it fails open — lints the just-edited file (`lint_cmd` file-scoped; falls back to `pre_commit.lint` whole-repo) and injects findings back as `additionalContext`, never blocking. All three, like `done`, derive their `check_service` validator allowlists from `*.model_fields` so config-key validity can't drift (knowledge `ca245d6a`). **Honesty:** `git commit --no-verify` bypasses the git hooks — these are quality gates, not enforcement boundaries.

**Project knowledge** (worktree-local lifecycle, #358/#462): The optional `knowledge` section enables a project knowledge file (`KNOWLEDGE.md`) for cross-session AI learning, with an append-only ratings **vote log** (`KNOWLEDGE.ratings.jsonl`) beside it. Both are **tracked** files — an attached session edits the copy in its worktree and the change rides to `main` with its PR. They are never copied into worktrees (a copy would manufacture a stale snapshot). Two mechanisms make concurrent branches merge cleanly: a wade-managed `merge=union` block in `.gitattributes` (`ensure_knowledge_merge_attributes`, ensured per attached bootstrap; committed so `main` carries it as the server-side backstop), and the append-only vote log (merging is concatenation → no vote lost). `wade knowledge add` appends an entry (blocked in a throwaway plan/deps session — no PR to carry it), `get` prints the annotated file, and `status` reports uncommitted knowledge/ratings changes scoped to just those paths. A pre-#358 `KNOWLEDGE.ratings.yml` is folded to the same scores on read (in memory, no write) and converted on the first ratings write to a byte-deterministic seeded `.jsonl` (the `.yml` is `git rm`'d). A `.wade.yml` migration strips the knowledge/ratings entries from any existing `hooks.copy_to_worktree` (paths are canonicalized before comparison — folding `.`/`..` — so `./KNOWLEDGE.md`, `docs/../KNOWLEDGE.md` and `KNOWLEDGE.md` all match; bootstrap's copy-exclusion applies the same `collapse_relative_path` policy so a redundant-`..` spelling can't slip past one and re-copy main's file). The path must stay inside the project root. The pure path/parse/validation helpers (`resolve_knowledge_path`, `resolve_ratings_path`, `parse_entries`, `validate_knowledge_file`) live in the leaf module `utils/knowledge_file.py` so lower layers can use them without importing the service; `done` runs `validate_knowledge_file` as a completion gate (refuses on duplicate entry IDs or unresolved conflict markers when `knowledge.enabled`) so a `merge=union`-corrupted file can't reach `main`.

**Detached-vote staging (#462):** `record_rating_for_session()` first validates
the entry against the detached worktree's committed knowledge snapshot, then
serializes a durable `RatingEvent` only to `.wade/knowledge-ratings-staged.jsonl`.
What *authorizes* staging is `is_throwaway_knowledge_session()`: a
`.wade/throwaway-session` marker **plus** a linked worktree on a detached HEAD.
The marker is written by the two parents that create such a worktree
(`plan()` and standalone `task deps`, via `mark_throwaway_knowledge_session`) —
i.e. exactly the two lifecycles that later call `flush_staged_ratings()`. A bare
detached HEAD is deliberately not enough: a primary checkout parked on one (CI,
`git checkout <sha>`, bisect) or a hand-made `git worktree add --detach` has no
parent that would ever flush the artefact, so a vote staged there would be
stranded — those write the ordinary ratings sidecar instead.
Read-only `knowledge get`, `status`, and `tag list` use that same local snapshot;
they never cross into the main checkout. The parent plan/deps lifecycle calls
`flush_staged_ratings()` before cleanup, holds the staging-log lock through its
snapshot, the locked main-spool transfer, and staging cleanup, then appends only
event IDs not already present and fsyncs. A live sibling's append therefore
waits until cleanup and creates a fresh log rather than being lost between the
snapshot and unlink. Every filesystem operation on a session-relative
`.wade/` path — the readiness probe, the marker writer
(`mark_throwaway_knowledge_session`, which **raises** on an escape), the staging
writer, and the flush's read + unlink — first rejects a parent that is a symlink
or resolves outside the session root (`path_escapes_session`): `.wade/` is an
ordinary checked-out path, so a symlink there would silently redirect a vote into
the main checkout (or make the post-transfer `unlink` delete an outside file),
and a preflight alone cannot stop one planted afterwards. The flush *skips* such
a path (success, nothing to hand off) rather than failing, since the writer's
identical check means nothing could legitimately be staged there and a failure
would strand the worktree on an unclearable condition. A failed or partially
completed handoff leaves the
throwaway worktree and its generated output in place for retry — and that retry
is real, not advisory: `wade plan` and standalone `wade task deps` both call
`report_retained_vote_recovery` (`services/knowledge_recovery.py`) **before**
creating their next worktree, which sweeps every linked worktree of the repo
still carrying the throwaway marker plus a staging log
(`flush_retained_staged_ratings`) and reports what it recovered. Discovery is
deterministic, so no resume command or user bookkeeping is needed. The sweep
never *removes* a worktree — a retained one may be a live sibling session — and
is safe to run against one anyway, because the flush skips event IDs already in
the main spool. Standalone deps
also fsyncs its returned edges to `.wade/deps-analysis-output.txt` before that
handoff, because stdout alone would be lost with the parent process. Duplicate
delivery of a new event is folded once, while legacy no-ID JSONL vote records
retain their historical duplicate-vote semantics. The next attached
bootstrap's existing ratings-only carry-forward remains the sole path that puts
tracked ratings into a PR. `knowledge status` reports staged votes from a
detached session in addition to canonical sidecar dirt, and carries an
unreadable staging log as its own `staging_error` state — a corrupt transport
log is a *failing handoff*, so it must never be collapsed into "0 staged votes"
and reported as a clean session.

`wade task deps` runs the `DEPS` check for **every** non-prompt agent cwd, not
only a worktree it created itself — a reused planning worktree with blocked
`.wade/` staging must stop delegation too. Cleanup differs by owner: a
standalone worktree this call created is removed (nothing was produced yet),
while a planning worktree is preserved for the parent `wade plan` lifecycle that
flushes its votes.

**Lifecycle readiness enforcement (#462):** Phase readiness is resolved by
`check_service.resolve_session_readiness(phase, cwd)` — it loads config, maps the
phase to its `ai.<command>` section (declared as `ReadinessRequirements.ai_command`
in `models/readiness.py`, validated against `AI_COMMAND_NAMES` so a typo cannot
silently fall back to `ai.default_tool`), selects the tool, and runs
`check_session_readiness`. The CLI layer only picks *which directory* to inspect.
The first-action check remains the
agent-facing diagnostic, but `implementation-session catchup`/`sync`/`done` and
`review-pr-comments-session sync`/`done`/`fetch`/`resolve` invoke the same
read-only phase check again in the process that is about to act. This matters
after a resume or an approval-policy/PATH/credential/network change: the Wade
process is contained by the same runtime as the AI agent and cannot assume the
launcher had more authority. A blocked lifecycle command prints the stable
readiness result before issuing its Git/GitHub operation; sync/catchup retain
their established preflight exit code while the other commands retain their
ordinary failure exit contract.

`done` is the one endpoint whose subject may not be the cwd: it is deliberately
runnable from the main checkout with a worktree-name/issue target or `--plan`,
where it resolves that worktree and operates there. Its gate therefore checks
the **resolved** worktree — `resolve_done_worktree()` previews it read-only and
non-raising (it never creates the issue a plan-*file* target would, and it
leaves every error message to `done` itself), and `require_ready(cwd=…)` runs
the phase check against it. Checking the cwd instead would report
`IN_MAIN_CHECKOUT` and make both recovery forms unusable. `None` — no target,
or nothing resolvable — falls back to the process cwd.

The `gh --version`, `gh auth status --active`, and read-only `gh api user` probes share
a short five-second subprocess timeout. A runtime that blackholes an executable
or network packet therefore returns the phase's named unavailable-capability
status rather than indefinitely blocking the session's first action.

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

## Planning Lifecycle (plan-file contract)

`wade plan` (`services/plan_service.py`) is a **two-phase** flow. The AI **never
creates issues** — it writes one `PLAN*.md` per issue to a plan directory, and
after it exits **wade** validates those files and persists the issues and draft
PRs itself. This is a deliberate determinism boundary (see *Determinism via
Services*): the agent authors plan content; code decides what becomes an issue.

**Phase 1 — generate plan files.** In a git repo the service creates a
detached-HEAD **planning worktree** (`git/worktree.py:create_detached_worktree`),
bootstraps its fixed plan workflow and frozen WORK/REVIEW bindings, and points the
AI at `<worktree>/.wade/plans/`. Isolating outputs to that subdirectory keeps
ordinary repo markdown (e.g. `README.md`) from being misread as a generated plan.
Outside a git repo it falls back to a `tempfile.mkdtemp(prefix="wade-plan-")`
temp dir, materializes the same session bundle inside that directory, and skips
draft-PR creation (except for Antigravity CLI, which requires a
guarded git planning worktree because its launch uses normal file writing mode with
WADE's plan-artifact PreToolUse guard rather than agy's brain-sandboxed native plan mode).
The launch prompt (`plan-session.md`) tells the agent to write a plan file per issue
and to **not** create the issues.

**Phase 2 — validate, then persist.** After the AI exits, wade discovers the
title-parseable files (`validate_plan_files`) and runs the **strict**
`_select_valid_plans` gate (`utils/plan_validation.py:validate_plan_dir`): every
plan must carry a `## Complexity` and a conventional-commit title prefix. Invalid
files are surfaced loudly and skipped — a TTY run confirms before proceeding with
the valid subset; a non-TTY / `yolo` run proceeds after the warning. For a
from-scratch plan, `_create_issues_from_plans` then, **per valid plan file**:

1. Creates a **lightweight task** (title + a brief context excerpt) via the
   configured provider's `create_task` — this may be a **non-GitHub** provider.
2. Adds the `complexity:X` label.
3. Bootstraps a **draft PR** carrying the full plan body (`bootstrap_draft_pr`),
   then appends the PR link to the task body. Draft-PR / review APIs always flow
   through GitHub (`gh`) regardless of the task provider: non-GitHub providers
   compose `GitHubPRDelegateMixin` (`providers/_pr_delegate.py`), so task CRUD
   uses the provider's own backend while PR/review operations delegate to `gh`.

**`--issue <id>` (attach / supersede).** With an issue id, the session is
pre-loaded with that issue's context (and the issue heading is persisted to
`.wade/plan-issue.md` so a resumed/compacted plan session can re-inject it). A
**single** valid plan is attached to the existing issue via a draft PR
(`_attach_plan_to_existing_issue`, preserving the original body and appending the
PR link); **multiple** plans **supersede** it — one new issue per plan
(`_supersede_issue_with_plans`). Only if every plan became an issue does it then
comment on the original and — unless `yolo` — prompt to close it as *not
planned*; declining that prompt leaves the original open even though every plan
succeeded. A partial split (some plans failed to become issues) always leaves
the original open, no prompt asked.

**Partial-plan preservation.** When the strict gate rejects a batch (all invalid,
or the user aborted a partial run) or a draft PR can't be persisted (e.g. an
unresolvable declared base), the generated `PLAN*.md` are salvaged to a stable
temp dir (`_preserve_generated_plans`) instead of being discarded with the
worktree — a one-line fix (a missing `## Complexity`) shouldn't force a full
re-plan. This covers the from-scratch multi-plan path and the single-plan attach
failure path. The `--issue` **supersede** path is the exception: on a partial
split, the failed plan files are *not* salvaged — `_supersede_issue_with_plans`
returns only the successful issue numbers, and the caller finalizes those and
removes the planning worktree/temp dir, discarding the failed plan(s) along with
it. On the full-success paths the planning worktree/temp dir is cleaned up.

**Automatic dependency analysis.** When a run produces **2+** issues,
`_finalize_issues` runs `deps_service.analyze_deps` over them and applies any
dependency edges it finds (reusing the planning worktree). A single-issue run
instead offers to start implementing it immediately.

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
- `--base <branch>` — Base branch to branch from, target, and merge into. Precedence at implement time: explicit `--base` (or the chain-derived base threaded by `--chain`) > the existing draft PR's base (read from the PR lookup's `baseRefName`, recorded at plan time from a plan file's optional `## Base Branch` section) > `config.project.main_branch` (or `detect_main_branch`). An explicit `--base` is validated for well-formedness up front (`is_valid_git_ref`, symmetric with the plan-declared path checked at plan-done) so a malformed value fails fast instead of surfacing later as a murkier "does not exist"; a chain-derived base is a generated branch name and always passes. Reading the base off the already-verified lookup (rather than a second `get_pr_base_branch` query, whose `None` conflates "no base" with "gh failed") avoids a silent fallback to `main`. The resolved base is persisted to `.wade/base_branch` before startup catchup so `sync`/`done` merge into it — and cleared when the effective base resolves back to `main` (e.g. `--base main` retargeting a reused worktree's PR), so a stale pin never keeps targeting the old base. `sync`/`done` treat the stored base as **authoritative**: they no longer gate on a local/`origin/<base>` existence check (which a single-branch / narrow-refspec clone can fail even for a valid base), and instead let the fetch/merge path surface an unresolvable base as an error rather than silently falling back to `main`. An explicit `--base` that differs from the draft PR's base retargets the PR via `update_pr_base`; a failed retarget aborts rather than leaving a stale base. Editing the PR base does **not** rewrite the head branch's ancestry, so a **scaffold-only** branch (only its scaffold commit beyond the old base) is first re-rooted on the new base and force-pushed (`reroot_scaffold_branch_for_retarget`, shared by the plan and implement retarget paths) — otherwise the old base's commits would leak into the new base's diff and merge into it. When the scaffold branch is **not** checked out it is moved with `git branch -f`; when it **is** checked out (the `wade implement --cd` case) it is re-rooted *in place* with a hard reset inside its worktree (`reset_worktree_hard`), since `git branch -f` refuses a checked-out branch. The reroot **aborts** rather than retarget onto a stale branch when it can't be done loss-free: an unresolvable old base (can't prove the branch is scaffold-only — a reset might discard commits), a checked-out worktree with uncommitted **tracked** changes (`has_tracked_changes`) or one whose path can't be resolved, or an unresolvable new base. A branch carrying **real work** (`_branch_has_real_work`: more than the scaffold commit past its base — measured against the local head or `origin/<branch>` when the head lives only on the remote) cannot be rewritten without discarding it, so `start()` guards that case — confirm in a TTY, else abort — instead of silently flipping the base and polluting the PR, mirroring the plan flow's `_base_retarget_is_safe`. `--chain` remains hidden and threads the previous task's branch as the next task's base (treated exactly like an explicit `--base`).

**`wade implementation-session done`:**
- `target` (positional) — Optional issue number, worktree name, or plan file path. When a file path is given, creates the issue first; when a number/name, finds the worktree; when omitted, detects from current branch.
- `--no-close` — Don't close the issue on merge.
- `--draft` — Create PR as draft.

**`wade implement-batch`:**
- `--model` — Pass a specific AI model to all parallel sessions.
- `--sandbox` / `--no-sandbox` — Pin the AI-runtime sandbox profile for every
  child session; when omitted, no flag is emitted and each child re-resolves its
  normal implementation default.
- `--skill` / `--review-skill` — Repeatable ordered bindings forwarded to every
  child implementation session.
- `--refresh-skills` — Explicitly refresh frozen bindings in resumed child
  sessions.

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
