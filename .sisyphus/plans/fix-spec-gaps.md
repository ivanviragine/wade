# Fix All Spec Gaps (SG-002)

## TL;DR

> **Quick Summary**: Add explicit `--plan <file>` flag to `wade work done` to match Bash interface. The flag resolves a plan file's title to find the matching worktree, then runs `done` against it — distinct from the positional `target` argument which creates a new issue from the file.
>
> **Deliverables**:
> - `--plan <file>` flag on `wade work done` CLI command
> - Service function `_resolve_worktree_from_plan(plan_file: Path) -> tuple[Path, str, str | None]` in `work_service.py`
> - TDD test coverage for both CLI wiring and service logic
>
> **Estimated Effort**: Short
> **Parallel Execution**: YES — 2 tasks in Wave 1 + final verification
> **Critical Path**: Task 1 (service logic) → Task 2 (CLI wiring) → Final Verification

---

## Context

### Original Request
Fix spec gap SG-002: Bash `wade work done` accepts an explicit `--plan <file>` flag that resolves a plan file's title to find the matching worktree, then runs `done` against it. Python's `wade work done` only has a positional `target` argument.

### Interview Summary
**Key Discussions**:
- Test strategy: TDD (RED → GREEN → REFACTOR)
- SG-001 was verified as a false positive — both Bash and Python return exit 0 for "already up-to-date"
- Only SG-002 remains as a genuine spec gap

**Research Findings**:
- Bash implementation at `lib/work/done.sh:763-806` (`_work_do_done_for_plan()`) — extracts title from plan's `# Title` heading, slugifies it, finds worktree by slug, then delegates to `_work_do_done()`
- Bash flag parsing at `lib/work/sync.sh:379` — `--plan` takes next arg as file path
- Python already handles plan files as positional `target` (lines 1078-1088 of `work_service.py`) — but that path CREATES an issue, not finds a worktree
- **Critical difference**: `--plan` = find existing worktree by title slug; positional target file = create new issue from file

### Metis Review
**Identified Gaps** (addressed):
- Must clarify semantic difference between `--plan <file>` and positional plan file — addressed in task descriptions
- Must handle case where plan file has no `# Title` heading — return clear error
- Must handle case where no worktree matches the slug — return clear error with hint

---

## Work Objectives

### Core Objective
Add `--plan <file>` flag to `wade work done` that resolves a plan file to its matching worktree, matching the Bash `wade work done --plan <file>` interface.

### Concrete Deliverables
- `--plan` flag in `cli/work.py` `done` command
- `_resolve_worktree_from_plan()` function in `work_service.py`
- TDD test files with comprehensive coverage

### Definition of Done
- [ ] `uv run pytest tests/ -v --ignore=tests/live` — ALL PASS, zero regressions
- [ ] `uv run mypy src/ --strict` — 0 errors
- [ ] `uv run ruff check src/` — 0 errors
- [ ] `uv run ruff format --check src/` — 0 errors
- [ ] `wade work done --plan path/to/PLAN.md` resolves worktree and runs done
- [ ] `wade work done --help` shows `--plan` in options

### Must Have
- `--plan <file>` flag on `work done` command
- Plan file title extraction (first `# Title` heading)
- Worktree lookup by slugified title
- Clear error for missing file, no heading, no matching worktree
- Delegate to existing `done()` logic after resolution

### Must NOT Have (Guardrails)
- MUST NOT change behavior of positional `target` argument — it still creates issues from plan files
- MUST NOT duplicate `_slugify()` logic — reuse existing `utils/slug.py`
- MUST NOT duplicate worktree lookup logic — reuse `find_worktree_path()` or `_find_worktree_by_slug()`
- MUST NOT change `done()` function signature beyond adding `plan_file` parameter
- MUST NOT rearrange existing imports — add new imports in alphabetical order

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: TDD (RED → GREEN → REFACTOR)
- **Framework**: pytest (via `uv run pytest`)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — both tasks can start in parallel):
├── Task 1: Service logic — _resolve_worktree_from_plan() [quick]
└── Task 2: CLI wiring — --plan flag + done() integration [quick]

Wave FINAL (After ALL tasks — independent review, 4 parallel):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 (service) | — | T2 | 1 |
| T2 (CLI) | T1 | F1-F4 | 1 |
| F1-F4 | T1-T2 | — | FINAL |

### Agent Dispatch Summary

- **Wave 1**: **2 tasks** — T1 → `quick`, T2 → `quick`
- **FINAL**: **4 tasks** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

> Implementation + Test = ONE Task. TDD: RED → GREEN → REFACTOR.
> EVERY task has: Recommended Agent Profile + Parallelization info + QA Scenarios.

- [ ] 1. Add _resolve_worktree_from_plan() service function

  **What to do**:
  - **RED**: Create test file `tests/unit/test_services/test_done_plan_flag.py`
  - Write test `test_resolve_plan_extracts_title()`: Given a plan file starting with `# My Feature Plan`, extracts title `"My Feature Plan"`
  - Write test `test_resolve_plan_no_heading_errors()`: Given a plan file with no `#` heading, raises ValueError or returns None with error message
  - Write test `test_resolve_plan_finds_worktree_by_slug()`: Given title "My Feature Plan" → slugified to "my-feature-plan" → `find_worktree_path("my-feature-plan")` called → returns worktree path, branch, and issue number
  - Write test `test_resolve_plan_no_matching_worktree_errors()`: When no worktree matches the slug, returns clear error
  - Write test `test_resolve_plan_file_not_found_errors()`: When plan file doesn't exist, returns clear error
  - Run tests → expect FAIL (RED)
  - **GREEN**: In `src/wade/services/work_service.py`, add function:
    ```python
    def _resolve_worktree_from_plan(
        plan_file: Path,
        project_root: Path | None = None,
    ) -> tuple[Path, str, str | None]:
        """Resolve a plan file to its matching worktree.

        Extracts the title from the first '# Title' heading,
        slugifies it, and finds the matching worktree.

        Returns (worktree_path, branch_name, issue_number_or_none).
        Raises ValueError if plan file invalid or no worktree found.
        """
        if not plan_file.is_file():
            raise ValueError(f"Plan file '{plan_file}' not found.")

        # Extract title from first # heading
        first_line = plan_file.read_text().split("\n", 1)[0].strip()
        import re
        match = re.match(r"^#\s+(.+)", first_line)
        if not match:
            raise ValueError(
                "Plan file must start with a '# Title' heading to derive the worktree name."
            )
        title = match.group(1).strip()

        # Slugify and find worktree
        from wade.utils.slug import slugify
        slug = slugify(title)

        wt_path = find_worktree_path(slug, project_root=project_root)
        if not wt_path:
            raise ValueError(
                f"No worktree found matching plan title '{title}' (slug: {slug}). "
                "Check active worktrees with: wade work list"
            )

        # Extract branch and issue from worktree
        branch = git_repo.get_current_branch(wt_path)
        issue_number = extract_issue_from_branch(branch)

        return wt_path, branch, issue_number
    ```
  - Place this near `find_worktree_path()` for locality
  - Run tests → expect PASS (GREEN)
  - **REFACTOR**: Move imports to top of function if possible. Ensure error messages match Bash wording

  **Must NOT do**:
  - MUST NOT reimplement slugify — import from `utils/slug.py`
  - MUST NOT reimplement worktree lookup — call `find_worktree_path()`
  - MUST NOT change `done()` in this task — that's Task 2

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small helper function with clear Bash reference
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (but T2 depends on this)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 2
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `src/wade/services/work_service.py:1040-1050` — `find_worktree_path()` function. Call this for worktree lookup by slug
  - `src/wade/utils/slug.py` — `slugify()` function for title-to-slug conversion
  - `src/wade/services/work_service.py:1078-1088` — existing plan file handling in `done()` (creates issue). Our function is DIFFERENT — it finds an existing worktree

  **Bash Reference**:
  - `lib/work/done.sh:763-806` — `_work_do_done_for_plan()`: reads first line, extracts `# Title`, slugifies, calls `_work_find_worktree_by_slug()`, then delegates to `_work_do_done()` with the resolved issue number

  **Test References**:
  - `tests/unit/test_services/` — service test patterns
  - `tests/conftest.py` — `tmp_git_repo` fixture for creating temporary git repos

  **WHY Each Reference Matters**:
  - `find_worktree_path()` — the existing lookup function we MUST reuse
  - `slug.py` — the existing slugify we MUST reuse (not create our own)
  - `done.sh:763-806` — the specification: title extraction → slugify → find worktree → delegate

  **Acceptance Criteria**:
  - [ ] Test file created: `tests/unit/test_services/test_done_plan_flag.py`
  - [ ] `uv run pytest tests/unit/test_services/test_done_plan_flag.py -v` → PASS (5 tests, 0 failures)
  - [ ] `_resolve_worktree_from_plan()` exists in `work_service.py`

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Plan file with valid title resolves to worktree
    Tool: Bash (uv run pytest)
    Preconditions: Temp plan file with "# My Feature Plan" heading, find_worktree_path mocked to return a path
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_done_plan_flag.py::test_resolve_plan_finds_worktree_by_slug -v`
      2. Assert PASSED
      3. Verify find_worktree_path was called with "my-feature-plan"
    Expected Result: Returns (worktree_path, branch, issue_number) tuple
    Failure Indicators: find_worktree_path called with wrong slug, or ValueError raised
    Evidence: .sisyphus/evidence/task-1-resolve-plan.txt

  Scenario: Plan file without heading raises clear error
    Tool: Bash (uv run pytest)
    Preconditions: Temp plan file with content "No heading here"
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_done_plan_flag.py::test_resolve_plan_no_heading_errors -v`
      2. Assert PASSED
      3. Verify ValueError message mentions "# Title"
    Expected Result: ValueError with descriptive message
    Failure Indicators: No error raised, or vague error message
    Evidence: .sisyphus/evidence/task-1-no-heading.txt
  ```

  **Commit**: YES
  - Message: `feat(work): add _resolve_worktree_from_plan() for --plan flag support`
  - Files: `src/wade/services/work_service.py`, `tests/unit/test_services/test_done_plan_flag.py`
  - Pre-commit: `uv run pytest tests/unit/test_services/test_done_plan_flag.py -v`

- [ ] 2. Wire --plan flag into CLI done command and service done()

  **What to do**:
  - **RED**: In test file `tests/unit/test_services/test_done_plan_flag.py` (created by Task 1):
  - Write test `test_done_with_plan_flag_resolves_and_delegates()`: When `done(plan_file=Path("PLAN.md"))` is called, `_resolve_worktree_from_plan()` is called, and `done()` proceeds with the resolved worktree/branch/issue
  - Write test `test_done_plan_flag_overrides_target()`: When both `target` and `plan_file` are given, `plan_file` takes precedence (same as Bash where `--plan` is separate from positional)
  - Write test `test_done_plan_flag_error_returns_false()`: When `_resolve_worktree_from_plan()` raises ValueError, `done()` prints error and returns False
  - Write test `test_cli_plan_flag_passes_to_service()`: CLI `done --plan /tmp/PLAN.md` passes `plan_file=Path("/tmp/PLAN.md")` to service `done()`
  - Run tests → expect FAIL (RED)
  - **GREEN**:
    1. In `src/wade/cli/work.py`, add `--plan` option to `done` command:
       ```python
       @work_app.command()
       def done(
           target: str | None = typer.Argument(None, help="Issue number, worktree name, or plan file."),
           plan_file: str | None = typer.Option(None, "--plan", help="Plan file to resolve worktree from."),
           no_close: bool = typer.Option(False, "--no-close", help="Don't close the issue on merge."),
           draft: bool = typer.Option(False, "--draft", help="Create PR as draft."),
           no_cleanup: bool = typer.Option(False, "--no-cleanup", help="Don't remove worktree."),
       ) -> None:
       ```
       Pass `plan_file=Path(plan_file) if plan_file else None` to `do_done()`
    2. In `src/wade/services/work_service.py`, update `done()` signature:
       ```python
       def done(
           target: str | None = None,
           plan_file: Path | None = None,
           no_close: bool = False,
           draft: bool = False,
           no_cleanup: bool = False,
           project_root: Path | None = None,
       ) -> bool:
       ```
    3. Add plan file resolution at the top of `done()`, BEFORE the existing target handling:
       ```python
       if plan_file:
           try:
               wt_path, branch, issue_num = _resolve_worktree_from_plan(
                   plan_file, project_root=project_root
               )
               console.step(f"Resolved from plan:")
               console.detail(f"Worktree: {wt_path}")
               console.detail(f"Branch: {branch}")
               target = issue_num
               cwd = wt_path
               # Skip the normal target/branch resolution below
           except ValueError as e:
               console.error(str(e))
               return False
       ```
  - Run tests → expect PASS (GREEN)

  **Must NOT do**:
  - MUST NOT change the positional `target` argument behavior
  - MUST NOT change the order of other flags
  - MUST NOT modify `_resolve_worktree_from_plan()` (Task 1's output)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small CLI flag addition + service integration
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO (depends on Task 1)
  - **Parallel Group**: Wave 1 (after Task 1)
  - **Blocks**: F1-F4
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `src/wade/cli/work.py:90-101` — current `done` command definition. Add `--plan` option here
  - `src/wade/services/work_service.py:1050-1120` — `done()` function. Add `plan_file` parameter and resolution logic at the top
  - `src/wade/services/work_service.py:1078-1088` — existing plan-file-as-target handling. Our `--plan` path is DIFFERENT and comes BEFORE this

  **Bash Reference**:
  - `lib/work/sync.sh:371-393` — Bash `done)` case: parses `--plan` flag, calls `_work_do_done_for_plan()` when plan_file is set, otherwise falls through to normal done logic

  **Test References**:
  - `tests/unit/test_services/test_done_plan_flag.py` — shared test file from Task 1

  **WHY Each Reference Matters**:
  - `cli/work.py:90-101` — exact insertion point for the `--plan` flag
  - `work_service.py:1050-1120` — the function to modify. Must understand control flow to insert plan resolution correctly
  - `sync.sh:371-393` — specification: `--plan` is a named flag, not a positional argument. When provided, calls a separate resolution path

  **Acceptance Criteria**:
  - [ ] `--plan` flag added to `done` command in CLI
  - [ ] `done()` service function accepts `plan_file: Path | None`
  - [ ] Plan file resolution happens before normal target handling
  - [ ] `uv run pytest tests/unit/test_services/test_done_plan_flag.py -v` → PASS (9 tests total, 0 failures)
  - [ ] `wade work done --help` shows `--plan` option

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: --plan flag resolves worktree and delegates to done
    Tool: Bash (uv run pytest)
    Preconditions: _resolve_worktree_from_plan mocked to return (Path("/tmp/wt"), "feat/42-my-plan", "42"), done logic mocked
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_done_plan_flag.py::test_done_with_plan_flag_resolves_and_delegates -v`
      2. Assert PASSED
      3. Verify _resolve_worktree_from_plan was called
      4. Verify done proceeded with issue_number="42"
    Expected Result: Plan file resolved, done delegated to resolved worktree
    Failure Indicators: _resolve_worktree_from_plan not called, or wrong issue number used
    Evidence: .sisyphus/evidence/task-2-plan-flag-done.txt

  Scenario: --plan flag error returns False
    Tool: Bash (uv run pytest)
    Preconditions: _resolve_worktree_from_plan raises ValueError("No worktree found")
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_done_plan_flag.py::test_done_plan_flag_error_returns_false -v`
      2. Assert PASSED
      3. Verify done() returns False
      4. Verify error message printed
    Expected Result: done() returns False with error message
    Failure Indicators: Exception propagated, or done() returns True
    Evidence: .sisyphus/evidence/task-2-plan-flag-error.txt

  Scenario: --plan shows in help output
    Tool: Bash (wade work done --help)
    Preconditions: wade installed
    Steps:
      1. Run `uv run python -m wade.cli.main work done --help`
      2. Check output contains "--plan"
      3. Check output contains "Plan file to resolve worktree from"
    Expected Result: --plan flag visible in help output
    Failure Indicators: --plan not shown or wrong help text
    Evidence: .sisyphus/evidence/task-2-help-output.txt
  ```

  **Commit**: YES
  - Message: `feat(work): add --plan flag to work done for worktree resolution by plan title`
  - Files: `src/wade/cli/work.py`, `src/wade/services/work_service.py`, `tests/unit/test_services/test_done_plan_flag.py`
  - Pre-commit: `uv run pytest tests/unit/test_services/test_done_plan_flag.py -v`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns. Check evidence files exist in `.sisyphus/evidence/`.
  Output: `Must Have [4/4] | Must NOT Have [4/4] | Tasks [2/2] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `uv run mypy src/ --strict` + `uv run ruff check src/` + `uv run pytest tests/ -v --ignore=tests/live`. Review changed files for type safety, error handling, unused imports.
  Output: `Mypy [PASS/FAIL] | Ruff [PASS/FAIL] | Tests [N pass/N fail] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Execute EVERY QA scenario from EVERY task. Test `wade work done --help` shows `--plan`. Save evidence to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  Verify 1:1 with plan: `--plan` flag added, resolution function added, positional target behavior unchanged. Flag any unaccounted changes.
  Output: `Tasks [2/2 compliant] | VERDICT`

---

## Commit Strategy

| Task | Commit Message | Files | Pre-commit Check |
|------|---------------|-------|-----------------|
| T1 | `feat(work): add _resolve_worktree_from_plan() for --plan flag support` | `services/work_service.py`, test file | `uv run pytest tests/unit/test_services/test_done_plan_flag.py -v` |
| T2 | `feat(work): add --plan flag to work done for worktree resolution by plan title` | `cli/work.py`, `services/work_service.py`, test file | `uv run pytest tests/unit/test_services/test_done_plan_flag.py -v` |

---

## Success Criteria

### Verification Commands
```bash
uv run pytest tests/ -v --ignore=tests/live   # Expected: ALL PASS, 0 failures
uv run mypy src/ --strict                      # Expected: Success, 0 errors
uv run ruff check src/                         # Expected: 0 errors
uv run ruff format --check src/                # Expected: 0 reformatted
uv run python -m wade.cli.main work done --help  # Expected: --plan flag visible
```

### Final Checklist
- [ ] `--plan <file>` flag present on `wade work done`
- [ ] Plan file title extraction works
- [ ] Worktree resolution by slug works
- [ ] Positional target behavior unchanged
- [ ] All tests pass (existing + new)
- [ ] Mypy strict passes
- [ ] Ruff passes
