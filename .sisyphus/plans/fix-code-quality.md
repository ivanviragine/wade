# Fix All Code Quality Issues (CQ-001 through CQ-011)

## TL;DR

> **Quick Summary**: Fix 11 code quality issues in Python wade that are unrelated to Bash parity but represent real bugs, silent failure modes, and robustness gaps. Covers error handling, concurrency safety, timeout protection, and defensive programming.
>
> **Deliverables**:
> - CQ-001: `get_conflicted_files()` raises on subprocess error instead of returning empty list
> - CQ-002: `get_pr_for_branch()` logs specific exceptions instead of swallowing all errors silently
> - CQ-003: Bootstrap hook subprocess gets 60-second timeout
> - CQ-004: Config migration pipeline is atomic — aborts all on failure, leaves file unchanged
> - CQ-005: `_get_ai_tool()` validates unknown values instead of silently coercing
> - CQ-006: SQLite `busy_timeout` set to 30000ms (30s) for parallel `wade` safety
> - CQ-007: All DB write operations (INSERT/UPDATE/DELETE) wrapped in explicit transactions
> - CQ-008: `__init_subclass__` warns on duplicate `TOOL_ID` registration instead of silently overwriting
> - CQ-009: `remove_worktree()` parameterized with `force: bool = True` (default preserves behavior)
> - CQ-010: `_done_via_direct()` prompts user to retry or skip on cleanup failure
> - CQ-011: `_slugify()` replaces Unicode chars with `-` instead of `?`
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 4 waves
> **Critical Path**: Wave 1 (isolated) → Wave 2 (callee) → Wave 3 (caller) → Wave 4 (compound) → Final Verification

---

## Context

### Original Request
Fix all code quality issues identified in the Bash-to-Python comparative analysis report (`.sisyphus/plans/bash-to-python-analysis.md` §7). These are Python-specific bugs and robustness gaps unrelated to Bash parity.

### Interview Summary
**Key Discussions**:
- Test strategy: Tests-after (implement fix first, then add tests verifying the new behavior)
- CQ-003 timeout: **60 seconds** — long enough for real setup scripts (npm install, brew, etc.), short enough to fail fast on hangs
- CQ-004 migration failure: **Atomic** — abort all migrations, leave file unchanged. A half-migrated config is worse than an unmigrated one.
- CQ-006 busy_timeout: **30000ms (30s)** — NOT configurable. Hardcoded. Enables safe parallel `wade` invocations in different terminals.
- CQ-007 scope: **Writes only** (INSERT/UPDATE/DELETE). SQLite WAL mode already gives safe concurrent reads; wrapping writes prevents races between parallel `wade` instances.
- CQ-009: Parameterize `remove_worktree(force: bool = True)` — default `True` preserves all existing behavior. Only one caller: `_cleanup_worktree()` at `work_service.py:1525`.
- CQ-010: Prompt user to retry or skip (not silent log, not hard abort).
- CQ-011: Replace Unicode with `-` not `?`. Do NOT add `unidecode` dependency.

### Metis Review
**Identified Guardrails** (must follow):
- MUST NOT change observable behavior or function signatures used by Plans 1 and 2
- CQ-009: Default `force=True` preserves all existing call sites — no callers need updating
- CQ-011: Do NOT add `unidecode` or any new dependency
- CQ-006: Hardcode `busy_timeout=30000`, do NOT make it configurable
- Import block conflicts: Add new imports in alphabetical order within their group. Never rearrange existing imports.
- Recommended merge order: Plan 3 → Plan 1 → Plan 2 (CQ cleanup first, bugs build on cleaner code)

---

## Work Objectives

### Core Objective
Improve robustness, error visibility, and concurrency safety of Python wade by fixing 11 code quality issues, verified via tests-after approach.

### Concrete Deliverables
- 11 targeted fixes across 9 Python modules
- Tests for each fix (tests-after: implement → test → verify)
- All existing tests continue to pass (zero regressions)

### Definition of Done
- [ ] `uv run pytest tests/ -v --ignore=tests/live` — ALL PASS, zero regressions
- [ ] `uv run mypy src/ --strict` — 0 errors
- [ ] `uv run ruff check src/` — 0 errors
- [ ] `uv run ruff format --check src/` — 0 errors
- [ ] Each fix has corresponding test(s) that verify the new behavior

### Must Have
- Subprocess error raises in `get_conflicted_files()` (CQ-001)
- Specific exception logging in `get_pr_for_branch()` (CQ-002)
- 60-second timeout on bootstrap hooks (CQ-003)
- Atomic migration pipeline (CQ-004)
- Unknown AI tool validation in `_get_ai_tool()` (CQ-005)
- SQLite `busy_timeout=30000` (CQ-006)
- Write transactions in DB repositories (CQ-007)
- Duplicate TOOL_ID warning in `__init_subclass__` (CQ-008)
- `force` parameter on `remove_worktree()` (CQ-009)
- User prompt on cleanup failure in `_done_via_direct()` (CQ-010)
- Unicode → `-` in `_slugify()` (CQ-011)

### Must NOT Have (Guardrails)
- **CQ-003**: MUST NOT change the hook invocation interface — only add `timeout=60` to the subprocess call
- **CQ-004**: MUST NOT change the migration function signatures — only wrap the pipeline in try/except with file restore
- **CQ-006**: MUST NOT make `busy_timeout` configurable — hardcode `30000`
- **CQ-007**: MUST NOT wrap read operations in transactions — writes only
- **CQ-009**: MUST NOT change the default value of `force` — keep `True` to preserve all existing behavior
- **CQ-011**: MUST NOT add `unidecode` or any new dependency
- **No refactoring beyond what's needed for the fix** — each change should be minimal and targeted
- **No rearranging existing imports** — add new imports in alphabetical order within their group

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: Tests-after (implement fix → write tests → verify pass)
- **Framework**: pytest (via `uv run pytest`)
- **Each task follows**: Implement fix → write tests verifying new behavior → run full suite

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/cq-task-{N}-{scenario-slug}.{ext}`.

- **Error handling**: Use pytest with `pytest.raises()` — assert correct exception type and message
- **Logging**: Use `caplog` fixture — assert log message and level
- **Timeout**: Use `unittest.mock.patch` to simulate subprocess timeout — assert `TimeoutExpired` is caught and re-raised
- **Transactions**: Use SQLite directly — assert partial writes are rolled back on failure
- **CLI output**: Use `uv run python -m wade.cli.main` — invoke command, check output

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — isolated, no callers affected):
├── Task 1: CQ-008 — Duplicate TOOL_ID warning [quick] (1 line, ai_tools/base.py)
├── Task 2: CQ-011 — Unicode slugify fix [quick] (1 line, git/branch.py)
├── Task 3: CQ-006 — SQLite busy_timeout [quick] (1 line, db/engine.py)
└── Task 4: CQ-005 — _get_ai_tool() validation [quick] (config/migrations.py)

Wave 2 (After Wave 1 — callee changes, no callers affected yet):
├── Task 5: CQ-001 — get_conflicted_files() raises on error [quick] (git/sync.py)
├── Task 6: CQ-002 — get_pr_for_branch() specific exception logging [quick] (providers/github.py)
└── Task 7: CQ-009 — remove_worktree() force param [quick] (git/worktree.py)

Wave 3 (After Wave 2 — caller changes, depend on callee fixes):
├── Task 8: CQ-010 — _done_via_direct() cleanup failure prompt [unspecified-high] (work_service.py)
└── Task 9: CQ-003 — Bootstrap hook timeout [quick] (work_service.py)

Wave 4 (After Wave 3 — compound/pipeline changes):
├── Task 10: CQ-004 — Atomic migration pipeline [unspecified-high] (config/migrations.py)
└── Task 11: CQ-007 — DB write transactions [unspecified-high] (db/repositories.py)

Wave FINAL (After ALL tasks — independent review, 4 parallel):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 (CQ-008) | — | — | 1 |
| T2 (CQ-011) | — | — | 1 |
| T3 (CQ-006) | — | T11 | 1 |
| T4 (CQ-005) | — | T10 | 1 |
| T5 (CQ-001) | — | — | 2 |
| T6 (CQ-002) | — | — | 2 |
| T7 (CQ-009) | — | T8 | 2 |
| T8 (CQ-010) | T7 | F1-F4 | 3 |
| T9 (CQ-003) | — | F1-F4 | 3 |
| T10 (CQ-004) | T4 | F1-F4 | 4 |
| T11 (CQ-007) | T3 | F1-F4 | 4 |
| F1-F4 | T1-T11 | — | FINAL |

### Agent Dispatch Summary

- **Wave 1**: **4 tasks** — T1 → `quick`, T2 → `quick`, T3 → `quick`, T4 → `quick`
- **Wave 2**: **3 tasks** — T5 → `quick`, T6 → `quick`, T7 → `quick`
- **Wave 3**: **2 tasks** — T8 → `unspecified-high`, T9 → `quick`
- **Wave 4**: **2 tasks** — T10 → `unspecified-high`, T11 → `unspecified-high`
- **FINAL**: **4 tasks** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

> Implementation + Test = ONE Task. Tests-after: implement fix → write tests → verify.
> EVERY task has: Recommended Agent Profile + Parallelization info + QA Scenarios.


- [ ] 1. CQ-008: Warn on duplicate TOOL_ID registration

  **What to do**:
  - **FIX**: In `src/wade/ai_tools/base.py`, in `__init_subclass__`, before writing to `_registry`, check if `TOOL_ID` already exists. If it does, emit a `warnings.warn(f"TOOL_ID '{cls.TOOL_ID}' already registered by {_registry[cls.TOOL_ID].__name__}; overwriting with {cls.__name__}", stacklevel=2)` and then overwrite (preserve existing overwrite behavior, just make it visible).
  - Add `import warnings` at the top of the file (alphabetical order within stdlib imports).
  - **TEST**: Create `tests/unit/test_ai_tools/test_base_registry.py`. Write `test_duplicate_tool_id_warns()` that registers two classes with the same `TOOL_ID` and asserts `warnings.warn` was called with the expected message. Write `test_duplicate_tool_id_overwrites()` that asserts the second registration wins (existing behavior preserved).
  - Run `uv run pytest tests/unit/test_ai_tools/test_base_registry.py -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not raise an exception — warn only, preserve overwrite behavior
  - Do not change the registry data structure

  **Reference**:
  - `src/wade/ai_tools/base.py:35-38` — `__init_subclass__` implementation

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: None

  **QA Scenarios**:
  1. Register two adapters with same TOOL_ID → assert `UserWarning` raised with correct message
  2. After duplicate registration → assert second adapter is in registry (overwrite preserved)
  3. Register two adapters with different TOOL_IDs → assert no warning


- [ ] 2. CQ-011: Fix Unicode replacement in `_slugify()`

  **What to do**:
  - **FIX**: In `src/wade/git/branch.py`, in `_slugify()`, find the line that replaces non-ASCII characters with `?`. Change the replacement character from `"?"` to `"-"`. The fix is a one-character change in the `re.sub` or `encode/decode` call.
  - Read `src/wade/git/branch.py:32-52` first to understand the exact implementation before editing.
  - **TEST**: Create `tests/unit/test_git/test_branch_slugify.py` (or add to existing if it exists). Write `test_slugify_unicode_becomes_dash()` that calls `_slugify("café feature")` and asserts the result contains `-` not `?`. Write `test_slugify_ascii_unchanged()` that asserts ASCII-only strings are unchanged.
  - Run `uv run pytest tests/unit/test_git/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not add `unidecode` or any new dependency
  - Do not change the overall slugify logic — only the replacement character

  **Reference**:
  - `src/wade/git/branch.py:32-52` — `_slugify()` implementation

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: None

  **QA Scenarios**:
  1. `_slugify("café feature")` → result contains `-`, not `?`
  2. `_slugify("hello world")` → `"hello-world"` (ASCII unchanged)
  3. `_slugify("日本語")` → all chars replaced with `-`, no `?`


- [ ] 3. CQ-006: Set SQLite `busy_timeout` to 30000ms

  **What to do**:
  - **FIX**: In `src/wade/db/engine.py:35-41`, find where the SQLite engine is created. Add `connect_args={"timeout": 30}` to the `create_engine()` call. SQLAlchemy's SQLite dialect accepts `timeout` in seconds (not milliseconds) — use `30` (seconds = 30000ms).
  - Read `src/wade/db/engine.py` first to understand the exact `create_engine()` call signature before editing.
  - **TEST**: Create `tests/unit/test_db/test_engine.py` (or add to existing). Write `test_engine_busy_timeout_is_30s()` that inspects the engine's `connect_args` and asserts `timeout == 30`. Write `test_engine_wal_mode_enabled()` to verify WAL mode is still set (regression guard).
  - Run `uv run pytest tests/unit/test_db/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not make the timeout configurable — hardcode `30`
  - Do not change WAL mode or any other engine settings

  **Reference**:
  - `src/wade/db/engine.py:35-41` — `create_engine()` call

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocks**: Task 11 (CQ-007 tests use the engine)

  **QA Scenarios**:
  1. Inspect engine `connect_args` → `timeout == 30`
  2. WAL mode still enabled (regression guard)
  3. Engine creation succeeds with no errors


- [ ] 4. CQ-005: Validate unknown values in `_get_ai_tool()`

  **What to do**:
  - **FIX**: In `src/wade/config/migrations.py:33-44`, in `_get_ai_tool()`, after reading the `ai.default_tool` value from the raw config dict, validate it against the known `AIToolID` enum values. If the value is not a valid `AIToolID`, raise a `ValueError(f"Unknown AI tool '{value}' in config. Valid values: {[t.value for t in AIToolID]}")` instead of silently coercing/returning it.
  - Read `src/wade/config/migrations.py:33-44` and `src/wade/models/ai.py` (for `AIToolID`) before editing.
  - Import `AIToolID` from `src/wade/models/ai.py` if not already imported (add in alphabetical order within local imports).
  - **TEST**: Create `tests/unit/test_config/test_migrations_validation.py` (or add to existing). Write `test_get_ai_tool_valid_value()` that passes a known tool ID and asserts it returns correctly. Write `test_get_ai_tool_unknown_value_raises()` that passes `"unknown_tool"` and asserts `ValueError` is raised with the expected message. Write `test_get_ai_tool_missing_key_returns_default()` that passes a config with no `ai.default_tool` and asserts a sensible default is returned (check existing behavior first).
  - Run `uv run pytest tests/unit/test_config/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not change the function signature
  - Do not change behavior for valid values or missing keys — only add validation for unknown values

  **Reference**:
  - `src/wade/config/migrations.py:33-44` — `_get_ai_tool()` implementation
  - `src/wade/models/ai.py` — `AIToolID` enum

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocks**: Task 10 (CQ-004 uses this function)

  **QA Scenarios**:
  1. `_get_ai_tool({"ai": {"default_tool": "claude"}})` → returns `"claude"` (valid)
  2. `_get_ai_tool({"ai": {"default_tool": "unknown_tool"}})` → raises `ValueError`
  3. `_get_ai_tool({})` → returns default (no crash)


- [ ] 5. CQ-001: `get_conflicted_files()` raises on subprocess error

  **What to do**:
  - **FIX**: In `src/wade/git/sync.py:103-109`, in `get_conflicted_files()`, find the `except` block that catches subprocess errors and returns `[]`. Change it to re-raise the exception (or raise a new `GitError` / `subprocess.CalledProcessError`) so callers know the command failed. Do NOT silently return an empty list on error — that hides real failures.
  - Read `src/wade/git/sync.py:103-109` first to understand the exact exception handling before editing.
  - Check if a `GitError` or similar custom exception class exists in the codebase (`grep` for `class GitError` or `class GitException`). If it exists, use it. If not, re-raise the original `subprocess.CalledProcessError`.
  - **TEST**: Create `tests/unit/test_git/test_sync_conflicted_files.py` (or add to existing). Write `test_get_conflicted_files_raises_on_subprocess_error()` that mocks `subprocess.run` to raise `CalledProcessError` and asserts the exception propagates (not swallowed). Write `test_get_conflicted_files_returns_list_on_success()` that mocks a successful run and asserts the correct file list is returned (regression guard).
  - Run `uv run pytest tests/unit/test_git/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not change the return type for the success path — still returns `list[str]`
  - Do not catch and swallow the error in a new way

  **Reference**:
  - `src/wade/git/sync.py:103-109` — `get_conflicted_files()` error handling

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6, 7)
  - **Blocks**: None

  **QA Scenarios**:
  1. Mock `subprocess.run` to raise `CalledProcessError` → assert exception propagates
  2. Mock `subprocess.run` to succeed with conflict output → assert correct list returned
  3. Mock `subprocess.run` to succeed with empty output → assert `[]` returned


- [ ] 6. CQ-002: Log specific exceptions in `get_pr_for_branch()`

  **What to do**:
  - **FIX**: In `src/wade/providers/github.py:326-336`, in `get_pr_for_branch()`, find the broad `except Exception` block that silently swallows all errors. Replace it with specific exception handling:
    - Catch `subprocess.CalledProcessError` separately — log at WARNING level: `logger.warning("get_pr_for_branch failed", branch=branch, error=str(e))`
    - Catch `json.JSONDecodeError` separately — log at WARNING level: `logger.warning("get_pr_for_branch: invalid JSON response", branch=branch, error=str(e))`
    - Re-raise any other unexpected exceptions (do not catch bare `Exception`)
  - Read `src/wade/providers/github.py:326-336` first to understand the exact exception handling.
  - Check how `logger` is set up in this file (likely `structlog.get_logger()`).
  - **TEST**: Create `tests/unit/test_providers/test_github_pr.py` (or add to existing). Write `test_get_pr_for_branch_logs_on_subprocess_error()` using `caplog` to assert a WARNING is logged when `subprocess.run` raises `CalledProcessError`. Write `test_get_pr_for_branch_logs_on_json_error()` using `caplog` to assert a WARNING is logged on `JSONDecodeError`. Write `test_get_pr_for_branch_returns_none_on_known_errors()` to assert `None` is returned (not raised) for the two known error types.
  - Run `uv run pytest tests/unit/test_providers/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not change the return type — still returns `PR | None`
  - Do not re-raise `CalledProcessError` or `JSONDecodeError` — log and return `None`
  - Do not catch bare `Exception` — only the two specific types

  **Reference**:
  - `src/wade/providers/github.py:326-336` — `get_pr_for_branch()` error handling

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 7)
  - **Blocks**: None

  **QA Scenarios**:
  1. Mock `subprocess.run` to raise `CalledProcessError` → assert WARNING logged, `None` returned
  2. Mock subprocess to return invalid JSON → assert WARNING logged, `None` returned
  3. Mock subprocess to raise `OSError` (unexpected) → assert exception propagates (not swallowed)
  4. Mock subprocess to succeed → assert PR object returned (regression guard)


- [ ] 7. CQ-009: Parameterize `remove_worktree()` with `force: bool = True`

  **What to do**:
  - **FIX**: In `src/wade/git/worktree.py:53-70`, in `remove_worktree()`, add a `force: bool = True` parameter. When `force=True`, keep the existing `--force` flag in the git command. When `force=False`, omit `--force`. Default is `True` to preserve all existing behavior.
  - Read `src/wade/git/worktree.py:53-70` first to understand the exact function signature and git command construction.
  - Verify the only caller is `_cleanup_worktree()` at `work_service.py:1525` — do NOT change that call site (it relies on the default `force=True`).
  - **TEST**: Create `tests/unit/test_git/test_worktree_remove.py` (or add to existing). Write `test_remove_worktree_default_uses_force()` that mocks `subprocess.run` and asserts `--force` is in the command when called with default args. Write `test_remove_worktree_force_false_omits_flag()` that asserts `--force` is NOT in the command when `force=False`. Write `test_remove_worktree_existing_caller_unaffected()` that calls `remove_worktree(path)` (no `force` arg) and asserts `--force` is still used (regression guard for existing callers).
  - Run `uv run pytest tests/unit/test_git/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not change the default value of `force` — must be `True`
  - Do not update the call site in `work_service.py` — it should continue to work unchanged

  **Reference**:
  - `src/wade/git/worktree.py:53-70` — `remove_worktree()` implementation
  - `src/wade/services/work_service.py:1525` — only caller (`_cleanup_worktree()`)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6)
  - **Blocks**: Task 8 (CQ-010 uses `remove_worktree`)

  **QA Scenarios**:
  1. `remove_worktree(path)` (default) → `--force` in git command
  2. `remove_worktree(path, force=False)` → `--force` NOT in git command
  3. `remove_worktree(path, force=True)` → `--force` in git command (explicit)


- [ ] 8. CQ-010: Prompt user on cleanup failure in `_done_via_direct()`

  **What to do**:
  - **FIX**: In `src/wade/services/work_service.py:1278-1285`, in `_done_via_direct()`, find the cleanup call that silently ignores failures. Wrap it in a try/except. On failure, use `ui/prompts.py` to prompt the user: `"Worktree cleanup failed: {error}. What would you like to do?"` with choices `["Retry", "Skip (leave worktree in place)"]`. If "Retry", attempt cleanup again once. If "Skip", log a warning and continue.
  - Read `src/wade/services/work_service.py:1278-1285` first to understand the exact cleanup call.
  - Read `src/wade/ui/prompts.py` to understand the available prompt functions (likely `select()` or `menu()`).
  - Import the prompt function if not already imported (alphabetical order within local imports).
  - **TEST**: Create `tests/unit/test_services/test_done_via_direct_cleanup.py`. Write `test_cleanup_failure_prompts_user()` that mocks `remove_worktree` to raise an exception and asserts the prompt function is called. Write `test_cleanup_failure_retry_succeeds()` that mocks the first call to fail and the second to succeed, asserts cleanup is called twice. Write `test_cleanup_failure_skip_logs_warning()` that mocks the prompt to return "Skip" and asserts a warning is logged and the function continues without raising.
  - Run `uv run pytest tests/unit/test_services/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not retry more than once — one retry attempt only
  - Do not raise an exception if the user chooses "Skip"
  - Do not change the overall `_done_via_direct()` flow for the success path

  **Reference**:
  - `src/wade/services/work_service.py:1278-1285` — cleanup call in `_done_via_direct()`
  - `src/wade/ui/prompts.py` — available prompt functions

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 9)
  - **Parallel Group**: Wave 3
  - **Blocks**: F1-F4

  **QA Scenarios**:
  1. Cleanup succeeds → no prompt shown (success path unchanged)
  2. Cleanup fails, user selects "Retry", retry succeeds → function completes normally
  3. Cleanup fails, user selects "Retry", retry also fails → prompt shown again? No — only one retry. After second failure, treat as "Skip".
  4. Cleanup fails, user selects "Skip" → warning logged, function continues
  5. `uv run mypy src/ --strict` → 0 errors (type annotations correct)


- [ ] 9. CQ-003: Add 60-second timeout to bootstrap hook subprocess

  **What to do**:
  - **FIX**: In `src/wade/services/work_service.py:130-147`, in `bootstrap_worktree()`, find the `subprocess.run()` call that executes the `post_worktree_create` hook. Add `timeout=60` to that call. Catch `subprocess.TimeoutExpired` and raise a descriptive error: `raise RuntimeError(f"Bootstrap hook timed out after 60 seconds: {hook_path}")`.
  - Read `src/wade/services/work_service.py:130-147` first to understand the exact subprocess call.
  - Add `import subprocess` if not already imported (it likely is — check first).
  - **TEST**: Create `tests/unit/test_services/test_bootstrap_hook_timeout.py` (or add to existing). Write `test_bootstrap_hook_timeout_raises()` that mocks `subprocess.run` to raise `subprocess.TimeoutExpired` and asserts `RuntimeError` is raised with the expected message. Write `test_bootstrap_hook_success_unaffected()` that mocks a successful run and asserts no exception is raised (regression guard).
  - Run `uv run pytest tests/unit/test_services/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not change the hook invocation interface or the hook path resolution
  - Do not catch `TimeoutExpired` silently — always re-raise as `RuntimeError`
  - Do not add timeout to any other subprocess calls in the file

  **Reference**:
  - `src/wade/services/work_service.py:130-147` — `bootstrap_worktree()` subprocess call

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 8)
  - **Parallel Group**: Wave 3
  - **Blocks**: F1-F4

  **QA Scenarios**:
  1. Mock `subprocess.run` to raise `TimeoutExpired` → assert `RuntimeError` raised with "60 seconds" in message
  2. Mock `subprocess.run` to succeed → assert no exception raised
  3. Mock `subprocess.run` to raise `CalledProcessError` (non-timeout failure) → assert original exception propagates (not wrapped)


- [ ] 10. CQ-004: Make config migration pipeline atomic

  **What to do**:
  - **FIX**: In `src/wade/config/migrations.py`, in `run_all_migrations()` (the function that runs all 7 migrations and writes back to file), wrap the entire migration sequence in a try/except. Before running any migrations, read the original file content into a variable. If any migration raises an exception, write the original content back to the file (restore), then re-raise the exception with a descriptive message: `raise RuntimeError(f"Migration failed at step {step_name}; config file restored to original. Error: {e}") from e`.
  - Read `src/wade/config/migrations.py:257-296` first to understand the exact `run_all_migrations()` implementation.
  - The restore must happen BEFORE re-raising — use `finally` block or explicit except+restore+raise pattern.
  - **TEST**: Create `tests/unit/test_config/test_migrations_atomic.py`. Write `test_migration_failure_restores_file()` that mocks one migration step to raise an exception and asserts the config file content is unchanged after the call. Write `test_migration_success_writes_file()` that runs all migrations successfully and asserts the file is updated (regression guard). Write `test_migration_failure_raises_runtime_error()` that asserts `RuntimeError` is raised with the step name in the message.
  - Run `uv run pytest tests/unit/test_config/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not change individual migration function signatures
  - Do not add rollback logic inside individual migration functions — only in `run_all_migrations()`
  - Do not suppress the exception — always re-raise after restoring

  **Reference**:
  - `src/wade/config/migrations.py:257-296` — `run_all_migrations()` implementation

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 11)
  - **Parallel Group**: Wave 4
  - **Blocks**: F1-F4

  **QA Scenarios**:
  1. Mock migration step 3 to raise → assert file content unchanged after call
  2. Mock migration step 3 to raise → assert `RuntimeError` raised with step name
  3. All migrations succeed → assert file updated (regression guard)
  4. `uv run mypy src/ --strict` → 0 errors


- [ ] 11. CQ-007: Wrap DB write operations in explicit transactions

  **What to do**:
  - **FIX**: In `src/wade/db/repositories.py:27-32` and throughout the repository classes, find all write operations (INSERT, UPDATE, DELETE — i.e., `session.add()`, `session.delete()`, `session.exec()` with INSERT/UPDATE/DELETE statements). Wrap each write operation in an explicit `with session.begin():` context manager (or use `session.begin()` / `session.commit()` / `session.rollback()` explicitly). Read operations (`session.exec()` with SELECT, `session.get()`) do NOT need transaction wrapping.
  - Read `src/wade/db/repositories.py` fully before editing to understand all repository methods and their current transaction handling.
  - Check if SQLModel sessions already auto-commit — if they do, the fix may be to add explicit `session.begin()` blocks to ensure atomicity.
  - **TEST**: Create `tests/unit/test_db/test_repository_transactions.py`. Write `test_write_is_atomic_on_failure()` that mocks a mid-write failure and asserts the partial write is rolled back (no partial state in DB). Write `test_read_does_not_use_transaction()` that asserts read operations work without `session.begin()` (regression guard). Write `test_concurrent_writes_do_not_corrupt()` that simulates two writes and asserts both complete correctly (uses the 30s timeout from CQ-006).
  - Run `uv run pytest tests/unit/test_db/ -v` → expect PASS.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → expect ALL PASS.

  **Must NOT do**:
  - Do not wrap read operations in transactions
  - Do not change the repository method signatures
  - Do not change the session creation logic in `db/engine.py`

  **Reference**:
  - `src/wade/db/repositories.py:27-32` — repository CRUD operations
  - `src/wade/db/engine.py` — session/engine creation (already fixed by Task 3)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 10)
  - **Parallel Group**: Wave 4
  - **Blocks**: F1-F4

  **QA Scenarios**:
  1. Mock mid-write failure → assert partial write rolled back
  2. Read operation → no transaction overhead (SELECT still works)
  3. Two sequential writes → both committed correctly
  4. `uv run mypy src/ --strict` → 0 errors


- [ ] F1. Final: Plan compliance audit

  **What to do**:
  - Read `.sisyphus/plans/fix-code-quality.md` (this file) in full.
  - Read every file modified by Tasks 1-11.
  - For each task, verify: (a) the fix matches the task description exactly, (b) no extra changes were made, (c) no guardrails were violated.
  - Report any deviations found.

  **Recommended Agent Profile**:
  - **Category**: `oracle` (read-only consultation)
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with F2, F3, F4)
  - **Parallel Group**: FINAL


- [ ] F2. Final: Code quality review

  **What to do**:
  - Run `uv run mypy src/ --strict` → assert 0 errors.
  - Run `uv run ruff check src/` → assert 0 errors.
  - Run `uv run ruff format --check src/` → assert 0 reformatted files.
  - Run `uv run pytest tests/ -v --ignore=tests/live` → assert ALL PASS.
  - Report any failures with full output.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with F1, F3, F4)
  - **Parallel Group**: FINAL


- [ ] F3. Final: Real manual QA

  **What to do**:
  - For each fix, construct a minimal reproduction scenario and verify the new behavior:
    1. CQ-001: Call `get_conflicted_files()` with a mocked failing subprocess → assert exception raised
    2. CQ-002: Call `get_pr_for_branch()` with a mocked failing subprocess → assert WARNING in logs, `None` returned
    3. CQ-003: Call `bootstrap_worktree()` with a hook that sleeps 61s (mocked) → assert `RuntimeError` raised
    4. CQ-004: Run `run_all_migrations()` with a mocked failing step → assert file unchanged
    5. CQ-005: Call `_get_ai_tool()` with `"unknown_tool"` → assert `ValueError` raised
    6. CQ-006: Inspect engine `connect_args` → assert `timeout == 30`
    7. CQ-007: Simulate mid-write failure → assert rollback
    8. CQ-008: Register duplicate TOOL_ID → assert `UserWarning` emitted
    9. CQ-009: Call `remove_worktree(path, force=False)` → assert `--force` not in command
    10. CQ-010: Mock cleanup failure in `_done_via_direct()` → assert prompt shown
    11. CQ-011: Call `_slugify("café")` → assert `-` not `?`
  - Report any scenario that does not behave as expected.

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with F1, F2, F4)
  - **Parallel Group**: FINAL


- [ ] F4. Final: Scope fidelity check

  **What to do**:
  - Read the analysis report at `.sisyphus/plans/bash-to-python-analysis.md` §7 (Code Quality Issues).
  - For each of the 11 CQ issues listed there, verify it has a corresponding task in this plan and that the task's fix description matches the analysis report's description.
  - Verify no tasks were added that are NOT in the analysis report (scope creep check).
  - Verify the "Must NOT Have" guardrails were respected (no `unidecode`, no configurable timeout, no transaction on reads, etc.).
  - Report any mismatches.

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with F1, F2, F3)
  - **Parallel Group**: FINAL

---

## Commit Strategy

| After | Commit Message |
|-------|---------------|
| Task 1 (CQ-008) | `fix: warn on duplicate AI tool TOOL_ID registration` |
| Task 2 (CQ-011) | `fix: replace Unicode chars with dash in branch slugify` |
| Task 3 (CQ-006) | `fix: set SQLite busy_timeout to 30s for parallel safety` |
| Task 4 (CQ-005) | `fix: validate unknown AI tool values in config migrations` |
| Task 5 (CQ-001) | `fix: raise on subprocess error in get_conflicted_files` |
| Task 6 (CQ-002) | `fix: log specific exceptions in get_pr_for_branch` |
| Task 7 (CQ-009) | `fix: parameterize remove_worktree force flag` |
| Task 8 (CQ-010) | `fix: prompt user on worktree cleanup failure in done` |
| Task 9 (CQ-003) | `fix: add 60s timeout to bootstrap hook subprocess` |
| Task 10 (CQ-004) | `fix: make config migration pipeline atomic` |
| Task 11 (CQ-007) | `fix: wrap DB write operations in explicit transactions` |
| All FINAL pass | `chore: code quality fixes verified (CQ-001 through CQ-011)` |

---

## Success Criteria

All of the following must be true before this plan is considered complete:

1. `uv run pytest tests/ -v --ignore=tests/live` → **ALL PASS**, zero regressions
2. `uv run mypy src/ --strict` → **0 errors**
3. `uv run ruff check src/` → **0 errors**
4. `uv run ruff format --check src/` → **0 reformatted files**
5. Each of the 11 CQ issues has a test that verifies the new behavior
6. No new dependencies added (especially no `unidecode`)
7. No function signatures changed in a way that breaks Plans 1 or 2
8. F1 compliance audit: no deviations from plan
9. F3 manual QA: all 11 scenarios pass
