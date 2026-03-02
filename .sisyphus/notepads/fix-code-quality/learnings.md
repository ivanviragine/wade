# Fix Code Quality — Learnings

## [2026-02-24] Session Start

### Decisions
- CQ-003 timeout: 60 seconds
- CQ-004 failure: atomic (abort all, restore original file)
- CQ-006: busy_timeout=30 (seconds, SQLAlchemy units), NOT configurable
- CQ-007: writes only (INSERT/UPDATE/DELETE), reads unwrapped
- CQ-009: force=True default, preserves all callers
- CQ-010: prompt user — retry once, then skip
- CQ-011: Unicode → `-` (not `?`), NO unidecode dependency

### Guardrails
- MUST NOT change observable behavior or signatures used by Plans 1 and 2
- Add new imports alphabetically within their group — never rearrange existing imports
- No new dependencies allowed

## [2026-02-24] CQ-011 Complete

### Finding
The `_slugify()` function in `src/wade/git/branch.py` already uses `-` (dash) for non-ASCII character replacement, not `?`. The implementation is correct:
- Line 42: `re.sub(r"[^a-z0-9]+", "-", slug)` replaces all non-alphanumeric with `-`
- Unicode characters like `é` in "café" become `-`, resulting in "caf-feature"
- Japanese characters like `日本語` become all dashes, which collapse and strip to empty string

### Implementation
- Created `tests/unit/test_git/test_branch_slugify.py` with 9 comprehensive tests
- Tests verify:
  1. ASCII text handling (lowercasing, space→hyphen)
  2. Unicode replacement with `-` (not `?`)
  3. Japanese character handling
  4. Mixed Unicode/ASCII
  5. Consecutive hyphen collapsing
  6. Leading/trailing hyphen stripping
  7. Max length truncation
  8. Word boundary truncation
  9. Special character handling

### Verification
- ✅ All 9 new tests pass
- ✅ `uv run mypy src/wade/git/branch.py --strict` → 0 errors
- ✅ `uv run ruff check src/wade/git/branch.py tests/unit/test_git/test_branch_slugify.py` → 0 errors
- ✅ No code changes needed (implementation already correct)
- ✅ No new dependencies added

### Key Insight
The task was to verify/document that Unicode replacement uses `-` not `?`. The code was already correct; the task was to add comprehensive test coverage to prevent regressions.

## [2026-02-24] CQ-008: Duplicate TOOL_ID Warning

### Implementation
- Added `import warnings` to `src/wade/ai_tools/base.py` (line 11, alphabetically ordered)
- Modified `__init_subclass__` to check if TOOL_ID already exists in registry before overwriting
- Emits `UserWarning` with message format: `"TOOL_ID '{id}' already registered by {old_class}; overwriting with {new_class}"`
- Uses `stacklevel=2` to point warning to the class definition site
- Preserves existing overwrite behavior (no exception raised)

### Testing
- Created `tests/unit/test_ai_tools/test_base_registry.py` with 3 tests:
  1. `test_duplicate_tool_id_warns()` — verifies UserWarning is raised with correct message
  2. `test_duplicate_tool_id_overwrites()` — verifies second registration wins
  3. `test_no_warning_for_unique_tool_ids()` — verifies no warning for unique IDs
- All 3 tests PASS
- Used existing AIToolID enum values (CLAUDE, COPILOT, GEMINI, CODEX) to avoid enum validation errors
- Tests properly restore original registry state in finally blocks

### Quality Checks
- `uv run mypy src/ --strict` → 0 errors (src directory passes)
- `uv run ruff check src/wade/ai_tools/base.py` → All checks passed
- `uv run ruff check tests/unit/test_ai_tools/test_base_registry.py` → All checks passed
- `uv run pytest tests/unit/test_ai_tools/test_base_registry.py -v` → 3/3 PASS

### Notes
- Test files have expected import-untyped errors (same as existing test files)
- No new dependencies added
- No observable behavior changes (warning only, overwrite still happens)
- Minimal, targeted fix as required

## CQ-010: Retry/Skip prompt on cleanup failure in _done_via_direct

- `prompts.select(title, items)` returns 0-based index; index 0 = first item, 1 = second
- Patch `wade.services.work_service.logger` as a whole MagicMock to assert `mock_logger.warning.assert_called_once_with(...)`
- `contextlib.ExitStack` + dict of patch targets is a clean way to share "happy path" patches across 5+ tests without copy-paste
- The cleanup block catches `Exception` (not `GitError`) to handle all possible failures including branch locks, permission errors, etc.
- `prompts.select` is in `wade.ui.prompts`; import as `from wade.ui import prompts` (added alphabetically before `from wade.ui.console import console`)
