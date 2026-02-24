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
