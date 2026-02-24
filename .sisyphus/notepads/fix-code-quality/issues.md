# Fix Code Quality — Issues

## [2026-02-24] Session Start
(No issues yet — populated as tasks complete)

## [2026-02-24] CQ-006 Complete

### Implementation
- Modified `src/ghaiw/db/engine.py`:
  - Added `"timeout": 30` to `connect_args` in `create_engine()` call (line 31)
  - Updated PRAGMA busy_timeout from 5000ms to 30000ms (line 39)
  - Both changes ensure 30-second timeout for parallel ghaiwpy safety

### Tests Added
- `test_engine_busy_timeout_is_30s()` — Verifies PRAGMA busy_timeout returns 30000ms
- `test_engine_wal_mode_enabled()` — Regression guard for WAL mode

### Verification
- ✅ All 4 TestEngine tests pass
- ✅ All 15 database tests pass
- ✅ All 89 unit model tests pass
- ✅ mypy strict check: 0 errors
- ✅ ruff lint: 0 errors
- ✅ No regressions in existing tests

### Notes
- SQLAlchemy SQLite timeout is in seconds (30 = 30000ms)
- PRAGMA busy_timeout is in milliseconds (30000)
- Result tuples from PRAGMA queries require indexing [0] to extract scalar

## [2026-02-24] CQ-005 Complete

### Implementation
- Modified `src/ghaiw/config/migrations.py`:
  - Added import: `from ghaiw.models.ai import AIToolID` (alphabetically ordered)
  - Updated `_get_ai_tool()` to validate string AI tool values against `AIToolID` enum
  - Validation only applies to string values (preserves backward compatibility for numeric values)
  - Raises `ValueError` with sorted list of valid tools when unknown string value is encountered
  - Both v2 (`ai.default_tool`) and v1 (`ai_tool`) keys are validated

### Tests Added
- Created `tests/unit/test_config/test_migrations_validation.py` with 17 comprehensive tests:
  - Valid tool values (claude, copilot, gemini, codex, antigravity)
  - Unknown tool values raise ValueError with helpful message
  - Missing/empty keys return None without error
  - Legacy v1 format validation
  - v2 precedence over v1
  - Numeric values converted to string without validation
  - Case-sensitive validation
  - Whitespace not trimmed

### Verification
- ✅ All 17 new validation tests pass
- ✅ All 9 existing TestGetAITool tests pass (backward compatible)
- ✅ All 131 config unit tests pass
- ✅ mypy strict check: 0 errors
- ✅ ruff lint: 0 errors
- ✅ No regressions in existing tests

### Notes
- Validation is string-specific to preserve backward compatibility with numeric values
- Error messages include sorted list of valid AIToolID values for user clarity
- Both v2 and v1 config formats are validated independently
- Function signature unchanged — no breaking changes to callers

## [2026-02-24] CQ-001 Complete

### Implementation
- Modified `src/ghaiw/git/sync.py`:
  - Changed `get_conflicted_files()` to raise `GitError` on subprocess failure (exit code != 0)
  - Previously returned empty list `[]` on error, now raises exception
  - Error message includes git command, exit code, and stderr output
  - Success path unchanged: still returns `list[str]`

### Tests Added
- Created `tests/unit/test_git/test_sync_conflicted_files.py` with 5 comprehensive tests:
  1. `test_get_conflicted_files_raises_on_subprocess_error()` — verifies GitError is raised on failure
  2. `test_get_conflicted_files_returns_list_on_success()` — verifies correct list returned on success
  3. `test_get_conflicted_files_empty_on_no_conflicts()` — verifies empty list when no conflicts
  4. `test_get_conflicted_files_filters_empty_lines()` — verifies empty lines are filtered
  5. `test_get_conflicted_files_calls_git_with_correct_args()` — verifies correct git args

### Bug Fix (Incidental)
- Fixed pre-existing indentation error in `src/ghaiw/git/worktree.py` line 54
  - Docstring for `remove_worktree()` was not indented, causing SyntaxError
  - This was blocking test collection

### Verification
- ✅ All 5 new tests pass
- ✅ All 17 git unit tests pass (including 3 existing worktree tests)
- ✅ `uv run mypy src/ --strict` → 0 errors
- ✅ `uv run ruff check` → 0 issues
- ✅ `uv run ruff format --check` → all formatted
- ✅ No regressions in existing tests

### Notes
- Used existing `GitError` class from `ghaiw.git.repo` (no new exceptions needed)
- Error message formatted to fit 100-char line limit (split across 3 lines)
- Tests use mocking to avoid actual git subprocess calls
- Incidental fix to worktree.py was necessary to unblock test collection

## [2026-02-24] CQ-002 Complete

### Implementation
- Modified `src/ghaiw/providers/github.py`:
  - Replaced broad `except CommandError` with two specific exception handlers
  - `except CommandError as e:` → logs WARNING with branch and error details, returns None
  - `except json.JSONDecodeError as e:` → logs WARNING with branch and error details, returns None
  - All other exceptions now propagate (not caught)
  - json module already imported (line 11)

### Tests Added
- Created `tests/unit/test_providers/test_github_pr.py` with 4 comprehensive tests:
  1. `test_get_pr_for_branch_returns_pr_on_success()` — Verifies successful PR retrieval
  2. `test_get_pr_for_branch_logs_warning_on_subprocess_error()` — Verifies CommandError logging
  3. `test_get_pr_for_branch_logs_warning_on_json_error()` — Verifies JSONDecodeError logging
  4. `test_get_pr_for_branch_propagates_unexpected_errors()` — Verifies OSError propagates

### Verification
- ✅ All 4 new tests pass
- ✅ All 43 provider tests pass (no regressions)
- ✅ `uv run mypy src/ --strict` → 0 errors
- ✅ `uv run ruff check src/ghaiw/providers/github.py` → 0 issues
- ✅ `uv run ruff format --check src/ghaiw/providers/github.py` → 0 issues

### Notes
- Replaced silent broad exception handling with specific, logged exceptions
- CommandError and JSONDecodeError are caught and logged; all others propagate
- Uses structlog for consistent logging with key=value pairs
- No new dependencies added
- Return type unchanged: still returns `dict[str, Any] | None`

## [2026-02-24] CQ-009: Add force parameter to remove_worktree()

### Implementation
- Added `force: bool = True` parameter to `remove_worktree()` in `src/ghaiw/git/worktree.py`
- When `force=True` (default): includes `--force` in git command
- When `force=False`: omits `--force` from git command
- Updated docstring to document the new parameter
- Updated log.info call to include force value

### Testing
- Created `tests/unit/test_git/test_worktree_remove.py` with 3 tests:
  1. `test_remove_worktree_default_uses_force()` — verifies default includes `--force`
  2. `test_remove_worktree_force_false_omits_flag()` — verifies `force=False` omits `--force`
  3. `test_remove_worktree_force_true_explicit()` — verifies explicit `force=True` includes `--force`
- All 3 tests PASS

### Verification
- ✅ `uv run pytest tests/unit/test_git/ -q --tb=short` → 17/17 PASS (no regressions)
- ✅ `uv run mypy src/ --strict` → 0 errors
- ✅ `uv run ruff check src/ghaiw/git/worktree.py tests/unit/test_git/test_worktree_remove.py` → 0 errors
- ✅ `uv run ruff format --check src/ghaiw/git/worktree.py tests/unit/test_git/test_worktree_remove.py` → 0 issues
- ✅ Call site at `work_service.py:1525` unchanged — uses default `force=True`, preserving all existing behavior

### Key Insight
Default parameter `force=True` ensures backward compatibility — all existing callers automatically get the `--force` behavior without any code changes.

## [2026-02-24] CQ-003: Bootstrap Hook Timeout

### Implementation
- Added `timeout=60` to `subprocess.run()` call in `bootstrap_worktree()` at line 141
- Added exception handling for `subprocess.TimeoutExpired` (line 144-145)
- Re-raises as `RuntimeError(f"Bootstrap hook timed out after 60 seconds: {hook_path}")` with `from e` chaining
- Preserved existing `subprocess.CalledProcessError` handling (non-timeout failures logged as warnings)

### Testing
- Created `tests/unit/test_services/test_bootstrap_hook_timeout.py` with 4 comprehensive tests:
  1. `test_bootstrap_hook_timeout_raises_runtime_error()` — verifies TimeoutExpired → RuntimeError with "60 seconds" message
  2. `test_bootstrap_hook_success_unaffected()` — verifies successful execution unaffected, timeout=60 passed to subprocess
  3. `test_bootstrap_hook_non_timeout_error_propagates()` — verifies CalledProcessError NOT wrapped (logged as warning)
  4. `test_bootstrap_hook_timeout_includes_hook_path()` — verifies hook path included in error message

### Verification
- ✅ All 4 new tests PASS
- ✅ `uv run mypy src/ghaiw/services/work_service.py --strict` → 0 errors
- ✅ `uv run ruff check` → All checks passed
- ✅ `uv run ruff format --check` → 2 files already formatted
- ✅ No new dependencies added
- ✅ No changes to function signature or observable behavior (except timeout enforcement)

### Key Insight
The timeout is enforced at the subprocess level, preventing hung hooks from blocking worktree creation indefinitely. The 60-second limit is reasonable for bootstrap operations (file copying + hook execution).

## CQ-004: Atomic run_all_migrations() (completed)

### Pattern applied
- Read `original_content` (str) inside the existing load try/except, then wrap the migration loop + file write in a second `try/except Exception` block.
- On failure: `config_path.write_text(original_content)` → `raise RuntimeError(...)  from e`. No `finally`; explicit except+restore+raise as required.

### Gotcha: ruff reformat
- After editing `migrations.py` via the Edit tool, `ruff format --check` flagged one formatting change (trailing whitespace / blank line inside the new try block). Always run `uv run ruff format src/` after edits, then re-verify.

### Test patterns confirmed
- `unittest.mock.patch` (string target `"ghaiw.config.migrations.<func>"`) correctly intercepts the module-level functions called inside the lambdas in `run_all_migrations`, because lambdas close over the name, not the value. Patching the module attribute replaces what the lambda resolves to at call time.
- `pytest.raises(RuntimeError, match="restored")` works for substring matching.
- 5 tests total (3 required + 2 bonus): restore, RuntimeError message, error chaining (__cause__), success writes file, error message embeds original exception text.

## [2026-02-24] CQ-007: Repository Write Transactions

### Finding
`repositories.py` already had correct explicit transaction management:
- All 11 write methods (`create`, `update`, `upsert`, `log`) already called `session.commit()` explicitly.
- All 13 read methods (`get`, `get_by_state`, `get_by_task`, `get_active`, etc.) correctly had no commit.
- Pattern used: `with Session(self.engine) as session: session.add(record); session.commit()` — this is the "equivalent commit/rollback pattern" accepted by the task spec.

### No Code Changes Were Needed
The file satisfied all requirements before CQ-007 was started. "Modified" expectation in the task spec is met by this verification + the test file creation.

### Test File Created
`tests/unit/test_db/test_repository_transactions.py` (5 tests):
1. `test_write_is_committed` — INSERT verified in fresh session
2. `test_read_does_not_affect_writes` — reads return None, no ghost rows
3. `test_sequential_writes_both_committed` — two INSERTs both visible in fresh session
4. `test_update_is_committed` — UPDATE verified in fresh session
5. `test_audit_log_write_is_committed` — AuditLogRepository.log() INSERT verified

### Key Pattern for Future Tests
When verifying commits, open a *brand-new* `Session` after the write to bypass SQLModel's identity-map cache. Otherwise you may get a false positive from cached state.
