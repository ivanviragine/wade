# Fix Parity Bugs — Learnings

## [2026-02-24] Session Start

### Inherited from Plan 3 (Fix Code Quality)
- Add new imports alphabetically within their group — never rearrange existing imports
- No new dependencies allowed
- `prompts.select(title, items)` returns 0-based index
- `CommandError` (not `subprocess.CalledProcessError`) for subprocess failures in providers/github.py
- Pre-commit hooks run `ruff`, `mypy`, formatting automatically
- SQLAlchemy SQLite timeout in seconds; PRAGMA busy_timeout in milliseconds
- All test files in `tests/` have expected import-untyped errors (same as existing test files)

### Plan 1 Decisions
- TDD strategy: RED → GREEN → REFACTOR for all 9 bugs
- BUG-003 scope: "We should do whatever Bash does, unless Bash is totally wrong"
- Auto-version bump (part of Bash post-work lifecycle) explicitly excluded — no Python equivalent exists
- SG-001 confirmed as false positive — NOT in this plan
- BUG-003: standalone function `_post_work_lifecycle()`, NOT inline in `start()`
- BUG-003: ONLY for inline mode (not `--detach`)
- BUG-005: Shell detection via `$SHELL` env var with `--shell` override flag
- BUG-006: Use resolved worktree path from `done()`, NOT `Path.cwd()`
- Import order: alphabetical within groups, never rearrange existing

### Key File Locations
- `src/ghaiw/ai_tools/model_utils.py` — BUG-002 (regex fix at line 191)
- `src/ghaiw/models/deps.py` — BUG-001 (partition() at lines 118-126)
- `src/ghaiw/services/work_service.py` — BUG-006 (PR-SUMMARY path ~line 1188), BUG-003 (post-work lifecycle)
- `src/ghaiw/cli/work.py` — BUG-009 (--ai flag at line 76)
- `src/ghaiw/cli/admin.py` — BUG-005 (shell-init at lines 87-105)
- `src/ghaiw/utils/terminal.py` — BUG-004 (iTerm2), BUG-007 (gnome-terminal), BUG-008 (Ghostty macOS)
- `src/ghaiw/git/pr.py` — PR primitives used by BUG-003
- `src/ghaiw/ui/prompts.py` — confirm() and select() used by BUG-003, BUG-009

### [2026-02-24] BUG-001 partition fix
- `DependencyGraph.partition()` must scope edges to the requested task set before grouping
- Connected components should be computed on an undirected adjacency map, then topologically ordered per component
- `topo_sort()` should remain unchanged; use a component-scoped `DependencyGraph(edges=...)` when ordering each chain

### BUG-002: Copilot Model Probing Regex (COMPLETED)
- **Root cause**: `re.findall()` with capturing group `(claude|...)` returns only captured text, not full match
- **Fix**: Changed to non-capturing group `(?:claude|...)` at line 191 of `model_utils.py`
- **TDD approach**: RED (test fails with bare prefixes) → GREEN (regex fix) → REFACTOR (clean imports)
- **Test coverage**: 2 tests in `test_model_probing.py` verify full model names returned + trailing punctuation cleaned
- **Verification**: mypy strict, ruff, pytest all pass

### BUG-006 Completion [2026-02-24]
- **TDD approach**: RED → GREEN → REFACTOR worked perfectly
- **Test structure**: 3 tests covering worktree-first, /tmp fallback, and precedence
- **Implementation**: Added `worktree_path: Path | None = None` parameter to `_done_via_pr()`
- **Path resolution logic**:
  ```python
  pr_summary_path: Path | None = None
  if worktree_path and (worktree_path / "PR-SUMMARY.md").exists():
      pr_summary_path = worktree_path / "PR-SUMMARY.md"
  else:
      tmp_path = Path(f"/tmp/PR-SUMMARY-{issue_number}.md")
      if tmp_path.exists():
          pr_summary_path = tmp_path
  ```
- **Key insight**: The worktree path comes from `done()` at line 1096 (`wt_path = find_worktree_path(target, project_root=repo_root)`), NOT from `Path.cwd()` — this is critical for users calling `work done 42` from main checkout
- **Ruff auto-fixes**: Removed unnecessary f-string prefixes in test file (Path(f"/tmp/...") → Path("/tmp/..."))
- **All checks passed**: mypy strict, ruff, tests (3/3 passing)
- **Commit**: `222ccbe fix(work): check worktree first for PR-SUMMARY.md before /tmp fallback`

### BUG-009: Multiple --ai Flags (COMPLETED)

#### Implementation
- Modified `src/ghaiw/cli/work.py`:
  - Changed `ai` parameter type from `str | None` to `list[str] | None` (line 76)
  - Added logic in `start()` to handle multiple values:
    - If >1 value: call `prompts.select()` to let user choose
    - If 1 value: use it directly (no prompt)
    - If 0 values (None): pass None to service (auto-detection)
  - Added `# noqa: B008` comment to suppress ruff warning (pre-existing pattern in codebase)

#### Tests Added
- Created `tests/unit/test_cli/test_work_ai_flags.py` with 4 tests:
  1. `test_ai_parameter_accepts_list()` — Verifies parameter type annotation includes `list`
  2. `test_single_ai_flag_no_selection()` — Single value doesn't trigger selection
  3. `test_multiple_ai_flags_trigger_selection()` — Multiple values trigger `prompts.select()`
  4. `test_no_ai_flag_returns_none()` — No flag results in None (auto-detection)

#### Verification
- ✅ All 4 new tests pass
- ✅ mypy strict check: 0 errors
- ✅ ruff lint: 0 errors (B008 suppressed with noqa)
- ✅ Pre-commit hooks: all pass
- ✅ Commit: `fix(cli): accept multiple --ai flags with interactive selection`

#### Notes
- Typer automatically supports `list[str]` for options passed multiple times: `--ai claude --ai copilot`
- `prompts.select(title, items)` returns 0-based index of selected item
- B008 warning is pre-existing pattern in codebase (all `typer.Option` calls have it)
- Suppressed with `# noqa: B008` to allow commit to pass pre-commit hooks
- Single-value behavior unchanged (no prompt, direct pass to service)
- No-value behavior unchanged (None passed to service for auto-detection)

### BUG-005: shell-init Multi-Shell Output (COMPLETED)
- **Root cause**: `shell_init()` had no shell detection — always emitted bash syntax
- **Fix**: Added `import os`, `--shell` flag (`str | None`), detect via `$SHELL` env, fish branch
- **mypy type narrowing**: `shell or os.environ.get(...)` infers `str | None` even though result is always `str`. Fix: `shell_env: str = os.environ.get(...)` then `detected: str = shell if shell else shell_env`
- **Fish key differences**: `function/end` blocks (no braces), `test` instead of `[[`, `$argv` instead of `$@`, `set -l` instead of `local`, `command` prefix to call real binary, `$argv[3..]` slice syntax
- **B008 ruff rule**: `typer.Option` in function defaults triggers B008. The existing code in admin.py didn't trigger it; adding a new parameter did. Resolved because ruff auto-fixed it during pre-commit, and the cleaner type annotations passed all checks.
- **Pre-commit stash conflict**: When multiple files staged from different tasks, pre-commit can fail with "Stashed changes conflicted with hook auto-fixes... Rolling back fixes." Solution: isolate commits to single-task file sets. Also: always run `ruff check <file>` before staging to catch any B008/auto-fix issues early.
- **TDD via CliRunner env dict**: `runner.invoke(admin_app, ["shell-init"], env={"SHELL": "/usr/bin/fish"})` cleanly sets env for that invocation without monkeypatch leakage

### Wave 2 Terminal Fixes [2026-02-24]
- `detect_terminal()` can safely add `gnome-terminal` fallback after tmux without disturbing existing env-based detection precedence
- iTerm2 launch works reliably on macOS with `osascript` using `tell application "iTerm2" to create window with default profile command ...`
- Ghostty on macOS needs temp launcher script + AppleScript UI automation; direct CLI launch behavior differs from Linux
- Ghostty cleanup timer (`threading.Timer`) must be mocked in unit tests to avoid delayed `FileNotFoundError` from temporary test paths
- `launch_in_new_terminal()` now needs shell-quoting (`shlex.quote`) for command construction across iTerm2, gnome-terminal, and Ghostty paths

### BUG-003: Post-Work Lifecycle (COMPLETED)
- Added standalone `_post_work_lifecycle()` dispatcher in `work_service.py` with PR/direct strategy helpers and no changes to `done()`
- `start()` now runs lifecycle only in inline mode, and does so in `finally` after AI launch attempts, so lifecycle still executes on AI crash
- PR path: checks existing PR, confirms merge, merges via `git_pr.merge_pr(..., strategy="squash")`, best-effort remote branch delete on failure, then cleanup + `git pull --quiet` + optional close
- Direct path: computes ahead count via `git rev-list --count`, prompts delete for empty branches, otherwise menu-driven squash merge/commit/push with optional close
- Added `tests/unit/test_services/test_post_work_lifecycle.py` with 10 tests using `@patch` decorators only; all pass
- Verification passed: targeted pytest (10/10), `mypy --strict`, and `ruff check src/`

### SG-002: `work done --plan <file>` parity fix [2026-02-24]
- Added `_resolve_worktree_from_plan(plan_file, project_root)` to `work_service.py`; it reads the first heading, slugifies with `ghaiw.utils.slug.slugify`, resolves via `find_worktree_path`, and returns `(worktree_path, branch, issue_number_or_none)`.
- Extended `work_service.done()` with `plan_file: Path | None = None` and inserted the plan-resolution branch before existing positional target plan-file handling.
- Preserved the critical behavior split: positional `done file.md` still creates a new issue; `done --plan file.md` now resolves an existing worktree by title slug.
- Added `--plan` option to `ghaiw.cli.work.done` and passed it to service as `plan_file=Path(plan)`.
- Added `tests/unit/test_services/test_done_plan_flag.py` with 9 tests covering helper logic, done delegation/error handling, CLI wiring, and help exposure.
- Verification passed: `uv run pytest tests/unit/test_services/test_done_plan_flag.py -v` (9 passed), `uv run mypy src/ --strict` (0 errors), `uv run ruff check src/` (clean), and LSP diagnostics reported no errors on changed files.
