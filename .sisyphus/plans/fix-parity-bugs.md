# Fix All Parity Bugs (BUG-001 through BUG-009)

## TL;DR

> **Quick Summary**: Fix 9 verified parity bugs where Python ghaiw-py diverges from Bash ghaiw behavior, covering dependency partitioning, AI model probing, post-work lifecycle, terminal launch support, multi-shell init, and PR-SUMMARY path resolution.
>
> **Deliverables**:
> - BUG-001: Correct connected-component partitioning in `DependencyGraph.partition()`
> - BUG-002: Fix Copilot model probing regex to return full model names
> - BUG-003: Implement post-work lifecycle prompt after AI tool exits (PR merge, cleanup, direct merge)
> - BUG-004: Add iTerm2 AppleScript launch code for `--detach` mode
> - BUG-005: Make `shell-init` output correct syntax for bash/zsh/fish
> - BUG-006: Fix PR-SUMMARY.md path to check worktree first, then `/tmp/` fallback
> - BUG-007: Add gnome-terminal detection and launch support
> - BUG-008: Fix Ghostty macOS to use AppleScript instead of subprocess
> - BUG-009: Accept multiple `--ai` flags with interactive selection
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 3 waves + final verification
> **Critical Path**: BUG-002 (trivial) → BUG-001 (isolated model) → BUG-006 (small, done path) → BUG-003 (large, post-work lifecycle) → Final Verification

---

## Context

### Original Request
Fix all parity bugs identified in the Bash-to-Python comparative analysis report (`.sisyphus/plans/bash-to-python-analysis.md` §5). These are cases where Python ghaiw-py diverges from documented Bash ghaiw behavior in a way that produces incorrect results.

### Interview Summary
**Key Discussions**:
- Test strategy: TDD (RED → GREEN → REFACTOR) for all bug fixes
- BUG-003 scope: "We should do whatever Bash does, unless Bash is totally wrong"
- Auto-version bump (part of Bash post-work lifecycle) explicitly excluded — no Python equivalent exists
- SG-001 was re-verified and confirmed as false positive — NOT included in this plan

**Research Findings**:
- Bash `_worktree_post_work_lifecycle()` at `lib/worktree.sh:122-268` fully mapped — handles PR strategy (merge, cleanup, pull, close issue) and direct strategy (count ahead, menu, merge+cleanup)
- Called from `_work_launch_tools_native()` at `lib/work/terminal.sh:203-210` after both terminal and GUI tool exits
- Python `start()` at `work_service.py:439-465` completely missing this lifecycle
- Test infrastructure exists: pytest with fixtures in `tests/conftest.py`

### Metis Review
**Identified Gaps** (addressed):
- BUG-003 must be standalone function, not inline — addressed in task structure
- BUG-003 must not duplicate `done()` logic — uses existing primitives
- BUG-003 must specify behavior for: no PR found, PR already merged, merge failure, user declines, AI crash exit
- BUG-003 only applies to inline mode (not `--detach`) — explicitly scoped
- BUG-005: Shell detection strategy must be specified — using `$SHELL` detection with `--shell` override
- BUG-006: CWD may not be worktree during `work done` — use resolved worktree path, not `Path.cwd()`
- Import block conflicts across plans — imports in alphabetical order within groups
- All 9 bugs re-verified against Bash source — all confirmed real

---

## Work Objectives

### Core Objective
Bring Python ghaiw-py to full behavioral parity with Bash ghaiw for 9 identified divergences, verified via TDD.

### Concrete Deliverables
- 9 bug fixes across 6 Python modules
- 9+ test files with comprehensive test coverage (TDD: RED → GREEN → REFACTOR)
- All existing tests continue to pass (zero regressions)

### Definition of Done
- [ ] `uv run pytest tests/ -v --ignore=tests/live` — ALL PASS, zero regressions
- [ ] `uv run mypy src/ --strict` — 0 errors
- [ ] `uv run ruff check src/` — 0 errors
- [ ] `uv run ruff format --check src/` — 0 errors
- [ ] Each bug fix has corresponding test(s) that fail before the fix and pass after

### Must Have
- Connected-component detection in `partition()` (BUG-001)
- Non-capturing regex group in Copilot probing (BUG-002)
- Post-work lifecycle with PR merge prompt and direct merge menu (BUG-003)
- iTerm2 AppleScript launch (BUG-004)
- Multi-shell `shell-init` output (BUG-005)
- Worktree-first PR-SUMMARY.md resolution (BUG-006)
- gnome-terminal detection and launch (BUG-007)
- Ghostty macOS AppleScript launch (BUG-008)
- Multiple `--ai` flag support with selection (BUG-009)

### Must NOT Have (Guardrails)
- **BUG-001**: MUST NOT modify `topo_sort()` — only `partition()` is buggy
- **BUG-003**: MUST NOT duplicate `work done` logic — call existing primitives (`git_pr` functions, `_cleanup_worktree`, `provider.close_task`)
- **BUG-003**: MUST NOT add auto-version bump — no Python equivalent exists. Add comment: `# NOTE: auto-version bump intentionally omitted — no Python equivalent exists`
- **BUG-003**: MUST NOT create PRs in the lifecycle — that's `done()`'s job. Only detect/merge existing PRs
- **BUG-003**: MUST NOT implement lifecycle for `--detach` mode — only inline mode
- **BUG-005**: MUST NOT break existing Bash output — zsh/fish support is additive
- **BUG-005**: MUST NOT add PowerShell support — bash/zsh/fish only
- **BUG-006**: MUST NOT search for multiple naming patterns — only `PR-SUMMARY.md` in worktree, then `/tmp/PR-SUMMARY-{issue}.md`
- **BUG-009**: MUST NOT change behavior when single `--ai` is provided — only add multi-selection when multiple values given
- **No refactoring beyond what's needed for the fix** — each change should be minimal and targeted
- **No rearranging existing imports** — add new imports in alphabetical order within their group

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES
- **Automated tests**: TDD (RED → GREEN → REFACTOR)
- **Framework**: pytest (via `uv run pytest`)
- **Each task follows**: Write failing test → implement minimal fix → verify pass → refactor if needed

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Model/Service logic**: Use Bash (`uv run pytest`) — run specific test, assert PASS
- **CLI commands**: Use Bash (`uv run python -m ghaiw.cli.main`) — invoke command, check output
- **Terminal utilities**: Use Bash — construct command, verify output format

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — isolated, independent fixes):
├── Task 1: BUG-002 — Fix Copilot regex [quick] (1 line fix)
├── Task 2: BUG-001 — Fix partition() connected components [deep] (algorithm)
├── Task 3: BUG-006 — Fix PR-SUMMARY.md path [quick] (small, isolated)
├── Task 4: BUG-009 — Multiple --ai flags [quick] (CLI layer)
└── Task 5: BUG-005 — Multi-shell shell-init [unspecified-high] (multi-shell output)

Wave 2 (After Wave 1 — terminal fixes, all independent):
├── Task 6: BUG-004 — iTerm2 AppleScript launch [quick]
├── Task 7: BUG-007 — gnome-terminal support [quick]
└── Task 8: BUG-008 — Ghostty macOS AppleScript [quick]

Wave 3 (After Wave 1 — depends on BUG-006 being done):
└── Task 9: BUG-003 — Post-work lifecycle [deep] (largest task)

Wave FINAL (After ALL tasks — independent review, 4 parallel):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)
```

### Dependency Matrix

| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 (BUG-002) | — | — | 1 |
| T2 (BUG-001) | — | — | 1 |
| T3 (BUG-006) | — | T9 | 1 |
| T4 (BUG-009) | — | — | 1 |
| T5 (BUG-005) | — | — | 1 |
| T6 (BUG-004) | — | — | 2 |
| T7 (BUG-007) | — | — | 2 |
| T8 (BUG-008) | — | — | 2 |
| T9 (BUG-003) | T3 | F1-F4 | 3 |
| F1-F4 | T1-T9 | — | FINAL |

### Agent Dispatch Summary

- **Wave 1**: **5 tasks** — T1 → `quick`, T2 → `deep`, T3 → `quick`, T4 → `quick`, T5 → `unspecified-high`
- **Wave 2**: **3 tasks** — T6 → `quick`, T7 → `quick`, T8 → `quick`
- **Wave 3**: **1 task** — T9 → `deep`
- **FINAL**: **4 tasks** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

> Implementation + Test = ONE Task. TDD: RED → GREEN → REFACTOR.
> EVERY task has: Recommended Agent Profile + Parallelization info + QA Scenarios.


- [ ] 1. BUG-002: Fix Copilot model probing regex

  **What to do**:
  - **RED**: Create test file `tests/unit/test_ai_tools/test_model_probing.py`
  - Write test `test_probe_copilot_models_returns_full_names()` that asserts `probe_copilot_models()` returns full model names like `"claude-sonnet-4.6"`, NOT bare prefixes like `"claude"`
  - Write test `test_probe_regex_non_capturing_group()` that directly tests the regex pattern against sample `copilot model list` output
  - Run tests → expect FAIL (RED)
  - **GREEN**: In `src/ghaiw/ai_tools/model_utils.py:191`, change the capturing group `(claude|gpt|gemini|codex|o[0-9])` to non-capturing: `(?:claude|gpt|gemini|codex|o[0-9])`
  - Run tests → expect PASS (GREEN)
  - **REFACTOR**: Consider using `re.finditer()` with `.group(0)` instead of `re.findall()` for clarity, if it improves readability

  **Must NOT do**:
  - Do not change the model name patterns themselves — only the group semantics
  - Do not change any other regex in the file

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single-line regex fix with straightforward test
  - **Skills**: `[]`
    - No special skills needed

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4, 5)
  - **Blocks**: None
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `src/ghaiw/ai_tools/model_utils.py:180-200` — `probe_copilot_models()` function with the buggy regex at line 191. The function runs `copilot model list` and uses `re.findall()` to extract model names from the output
  - `src/ghaiw/ai_tools/model_utils.py:170-178` — `probe_claude_models()` for comparison pattern — uses simpler regex without this issue

  **Test References**:
  - `tests/unit/test_ai_tools/` — directory for AI tool tests. Check if it exists; if not, create it with `__init__.py`
  - `tests/conftest.py` — shared fixtures. This test likely needs no fixtures beyond `monkeypatch` for mocking subprocess output

  **External References**:
  - Python docs: `re.findall()` returns list of groups when pattern has groups, vs list of full matches when it doesn't — this is the root cause

  **WHY Each Reference Matters**:
  - `model_utils.py:191` — the exact line to fix. The regex group `()` makes `findall()` return captured groups, not full matches
  - `probe_claude_models()` — shows the correct pattern to follow (no capturing group issue there)

  **Acceptance Criteria**:
  - [ ] Test file created: `tests/unit/test_ai_tools/test_model_probing.py`
  - [ ] `uv run pytest tests/unit/test_ai_tools/test_model_probing.py -v` → PASS (2+ tests, 0 failures)
  - [ ] Regex at `model_utils.py:191` uses `(?:...)` non-capturing group

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Probe returns full model names, not prefixes
    Tool: Bash (uv run pytest)
    Preconditions: Test file exists with mocked subprocess output for `copilot model list`
    Steps:
      1. Run `uv run pytest tests/unit/test_ai_tools/test_model_probing.py::test_probe_copilot_models_returns_full_names -v`
      2. Assert output contains "PASSED"
      3. Verify the test asserts returned list contains full model name strings (e.g., "claude-sonnet-4.6"), not bare prefixes ("claude")
    Expected Result: Test PASSES — full model names returned
    Failure Indicators: Test FAILS with AssertionError showing bare prefix strings
    Evidence: .sisyphus/evidence/task-1-copilot-regex-pass.txt

  Scenario: Regex change does not break other probing functions
    Tool: Bash (uv run pytest)
    Preconditions: All existing tests still present
    Steps:
      1. Run `uv run pytest tests/unit/test_ai_tools/ -v`
      2. Assert all tests pass
    Expected Result: 0 failures across all AI tool tests
    Failure Indicators: Any FAILED test in the output
    Evidence: .sisyphus/evidence/task-1-no-regression.txt
  ```

  **Commit**: YES
  - Message: `fix(ai): correct copilot model probing regex to use non-capturing group`
  - Files: `src/ghaiw/ai_tools/model_utils.py`, `tests/unit/test_ai_tools/test_model_probing.py`
  - Pre-commit: `uv run pytest tests/unit/test_ai_tools/test_model_probing.py -v`

- [ ] 2. BUG-001: Fix DependencyGraph.partition() to compute connected components

  **What to do**:
  - **RED**: Create test file `tests/unit/test_models/test_deps_partition.py`
  - Write test `test_disconnected_subgraphs_form_separate_chains()`: Given edges A→B, C→D and tasks [A,B,C,D], `partition()` returns `([], [[A,B], [C,D]])` — two separate chains, NOT one `[[A,B,C,D]]`
  - Write test `test_single_connected_component()`: Given edges A→B, B→C and tasks [A,B,C], `partition()` returns `([], [[A,B,C]])` — one chain (correct current behavior for this case)
  - Write test `test_mixed_independent_and_dependent()`: Given edges A→B and tasks [A,B,C], `partition()` returns `([C], [[A,B]])` — C is independent
  - Write test `test_no_edges()`: Given no edges and tasks [A,B,C], `partition()` returns `([A,B,C], [])` — all independent
  - Write test `test_single_node_with_edge_target_outside_set()`: Given edge A→X and tasks [A] (X not in set), `partition()` returns `([A], [])` — A is independent
  - Run tests → expect first test FAILS (RED), others may pass
  - **GREEN**: Rewrite `partition()` in `src/ghaiw/models/deps.py:118-126`:
    1. Filter edges to only those where both endpoints are in the task set
    2. Build adjacency list (undirected) from filtered edges
    3. BFS/DFS from each unvisited node to find connected components
    4. For each component with edges: topological sort using existing `topo_sort()` method
    5. Single-node components with no edges go to the independent list
  - Run tests → expect ALL PASS (GREEN)
  - **REFACTOR**: Ensure the BFS/DFS is clean and readable. No changes to `topo_sort()` itself

  **Must NOT do**:
  - MUST NOT modify `topo_sort()` — only `partition()` is buggy
  - MUST NOT change the return type signature of `partition()` — it must still return `tuple[list[str], list[list[str]]]`

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Algorithm replacement (connected components + topo sort) requires careful graph reasoning
  - **Skills**: `[]`
    - No special skills needed — pure algorithm

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4, 5)
  - **Blocks**: None
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `src/ghaiw/models/deps.py:118-126` — current `partition()` implementation. The bug: it builds a single `chain` by walking ALL edges linearly, instead of grouping by connected component
  - `src/ghaiw/models/deps.py:95-116` — `topo_sort()` method. DO NOT MODIFY, but call it per-component in the new `partition()`
  - `src/ghaiw/models/deps.py:80-93` — `DependencyGraph` class definition with `edges: list[DependencyEdge]` field. Edges have `from_task: str` and `to_task: str`

  **Test References**:
  - `tests/unit/test_models/` — directory for model tests. Existing tests here can show patterns for testing Pydantic models

  **External References**:
  - Standard BFS connected components algorithm — O(V+E), well-known. Use `collections.deque` for BFS

  **WHY Each Reference Matters**:
  - `deps.py:118-126` — the exact code to replace. Understanding the current linear walk is essential to not regress
  - `deps.py:95-116` — `topo_sort()` is used BY the new `partition()` to order nodes within each component. Must understand its input/output contract
  - `deps.py:80-93` — the `DependencyEdge` model defines the edge structure used by partition

  **Acceptance Criteria**:
  - [ ] Test file created: `tests/unit/test_models/test_deps_partition.py`
  - [ ] `uv run pytest tests/unit/test_models/test_deps_partition.py -v` → PASS (5 tests, 0 failures)
  - [ ] `partition()` returns separate chains for disconnected subgraphs
  - [ ] `topo_sort()` is NOT modified

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Disconnected subgraphs produce separate chains
    Tool: Bash (uv run pytest)
    Preconditions: DependencyGraph with edges A→B, C→D and task set [A,B,C,D]
    Steps:
      1. Run `uv run pytest tests/unit/test_models/test_deps_partition.py::test_disconnected_subgraphs_form_separate_chains -v`
      2. Assert output contains "PASSED"
      3. The test verifies partition returns `([], [[A,B], [C,D]])` or `([], [[C,D], [A,B]])` (order between chains doesn't matter)
    Expected Result: Two separate chains, NOT one combined chain
    Failure Indicators: AssertionError showing `[[A,B,C,D]]` — all in one chain
    Evidence: .sisyphus/evidence/task-2-disconnected-subgraphs.txt

  Scenario: Mixed independent and dependent tasks
    Tool: Bash (uv run pytest)
    Preconditions: DependencyGraph with edge A→B and task set [A,B,C]
    Steps:
      1. Run `uv run pytest tests/unit/test_models/test_deps_partition.py::test_mixed_independent_and_dependent -v`
      2. Assert output contains "PASSED"
    Expected Result: `([C], [[A,B]])` — C independent, A→B as chain
    Failure Indicators: C appears in a chain, or A,B,C all in one chain
    Evidence: .sisyphus/evidence/task-2-mixed-partition.txt

  Scenario: Edge target outside task set
    Tool: Bash (uv run pytest)
    Preconditions: DependencyGraph with edge A→X and task set [A] (X not in set)
    Steps:
      1. Run `uv run pytest tests/unit/test_models/test_deps_partition.py::test_single_node_with_edge_target_outside_set -v`
      2. Assert output contains "PASSED"
    Expected Result: `([A], [])` — A is independent since X isn't in the batch
    Failure Indicators: A appears in a chain
    Evidence: .sisyphus/evidence/task-2-edge-outside-set.txt
  ```

  **Commit**: YES
  - Message: `fix(models): implement connected-component partitioning in DependencyGraph`
  - Files: `src/ghaiw/models/deps.py`, `tests/unit/test_models/test_deps_partition.py`
  - Pre-commit: `uv run pytest tests/unit/test_models/test_deps_partition.py -v`

- [ ] 3. BUG-006: Fix PR-SUMMARY.md path to check worktree first

  **What to do**:
  - **RED**: Create test file `tests/unit/test_services/test_pr_summary_path.py`
  - Write test `test_pr_summary_found_in_worktree()`: When `PR-SUMMARY.md` exists in worktree root, `_done_via_pr` uses that path (not `/tmp/`)
  - Write test `test_pr_summary_fallback_to_tmp()`: When `PR-SUMMARY.md` does NOT exist in worktree, falls back to `/tmp/PR-SUMMARY-{issue}.md`
  - Write test `test_pr_summary_worktree_takes_precedence()`: When BOTH exist, worktree version is used
  - Run tests → expect FAIL (RED)
  - **GREEN**: In `src/ghaiw/services/work_service.py`, modify `_done_via_pr()` around line 1188:
    1. Resolve the worktree path for this branch (from `git worktree list` or the `wt_path` already resolved in `done()`)
    2. Check `worktree_path / "PR-SUMMARY.md"` first
    3. If not found, fall back to `Path(f"/tmp/PR-SUMMARY-{issue_number}.md")`
    4. Pass the resolved path to `_build_pr_body()`
  - Note: The worktree path must come from the resolved `wt_path` in `done()` (line 1094), NOT from `Path.cwd()` — user may call `work done 42` from main checkout
  - Run tests → expect PASS (GREEN)

  **Must NOT do**:
  - MUST NOT search for multiple naming patterns (PR_SUMMARY, pr-summary, etc.) — only `PR-SUMMARY.md`
  - MUST NOT change `_build_pr_body()` signature unnecessarily

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small path resolution change with clear before/after
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4, 5)
  - **Blocks**: Task 9 (BUG-003 references PR-SUMMARY path handling)
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `src/ghaiw/services/work_service.py:1188` — current hardcoded path: `Path(f"/tmp/PR-SUMMARY-{issue_number}.md")`. This is the line to change
  - `src/ghaiw/services/work_service.py:1080-1100` — `done()` function resolves `wt_path` at line 1094. This path should be passed to `_done_via_pr()`
  - `src/ghaiw/services/work_service.py:1147-1155` — `_done_via_pr()` signature. May need to add `worktree_path: Path | None` parameter

  **Bash Reference**:
  - `lib/work/done.sh` — Bash looks for PR-SUMMARY.md in worktree CWD first, since the AI agent writes it there during the work session

  **Test References**:
  - `tests/unit/test_services/` — existing service test files for pattern reference (mocking, fixtures)

  **WHY Each Reference Matters**:
  - `work_service.py:1188` — the exact line to fix, currently hardcoded to `/tmp/`
  - `work_service.py:1080-1100` — `done()` already resolves worktree path, needs to pass it down
  - `work_service.py:1147-1155` — `_done_via_pr` signature may need updating to accept worktree path

  **Acceptance Criteria**:
  - [ ] Test file created: `tests/unit/test_services/test_pr_summary_path.py`
  - [ ] `uv run pytest tests/unit/test_services/test_pr_summary_path.py -v` → PASS (3 tests, 0 failures)
  - [ ] PR-SUMMARY.md checked in worktree path first, `/tmp/` as fallback

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: PR-SUMMARY.md found in worktree root
    Tool: Bash (uv run pytest)
    Preconditions: Mock worktree at `/tmp/test-wt-42/` with `PR-SUMMARY.md` containing "## Summary\nTest PR body"
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_pr_summary_path.py::test_pr_summary_found_in_worktree -v`
      2. Assert PASSED
      3. Test verifies `_build_pr_body()` received the worktree path, not `/tmp/PR-SUMMARY-42.md`
    Expected Result: Worktree path used for PR-SUMMARY.md
    Failure Indicators: Test shows `/tmp/PR-SUMMARY-42.md` was used instead
    Evidence: .sisyphus/evidence/task-3-worktree-summary.txt

  Scenario: Fallback to /tmp when not in worktree
    Tool: Bash (uv run pytest)
    Preconditions: No `PR-SUMMARY.md` in worktree root, but `/tmp/PR-SUMMARY-42.md` exists
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_pr_summary_path.py::test_pr_summary_fallback_to_tmp -v`
      2. Assert PASSED
    Expected Result: Falls back to `/tmp/PR-SUMMARY-42.md`
    Failure Indicators: FileNotFoundError or empty PR body
    Evidence: .sisyphus/evidence/task-3-tmp-fallback.txt
  ```

  **Commit**: YES
  - Message: `fix(work): check worktree first for PR-SUMMARY.md before /tmp fallback`
  - Files: `src/ghaiw/services/work_service.py`, `tests/unit/test_services/test_pr_summary_path.py`
  - Pre-commit: `uv run pytest tests/unit/test_services/test_pr_summary_path.py -v`

---

- [ ] 4. BUG-009: Accept multiple --ai flags with interactive selection

  **What to do**:
  - **RED**: Create test file `tests/unit/test_cli/test_work_ai_flags.py`
  - Write test `test_single_ai_flag_works_unchanged()`: Passing single `--ai claude` works as before
  - Write test `test_multiple_ai_flags_accepted()`: Passing `--ai claude --ai copilot` is accepted by CLI
  - Write test `test_multiple_ai_shows_selection()`: When multiple `--ai` values given, an interactive selection prompt is shown (mock `ui/prompts.py:select()`)
  - Write test `test_no_ai_flag_auto_detects()`: When no `--ai` given, auto-detects first installed tool (current behavior preserved)
  - Run tests → expect FAIL for multi-flag tests (RED)
  - **GREEN**: In `src/ghaiw/cli/work.py:76`:
    1. Change `ai: str | None = typer.Option(None, "--ai", ...)` to `ai: list[str] | None = typer.Option(None, "--ai", ...)`
    2. In the `start` command handler, if `len(ai) > 1`: call `ui_prompts.select()` to let user pick one
    3. If `len(ai) == 1`: use that tool directly (current behavior)
    4. If `ai is None` or empty: use config default (current behavior)
  - Run tests → expect PASS (GREEN)

  **Must NOT do**:
  - MUST NOT change behavior when single `--ai` is provided — must work exactly as before
  - MUST NOT change behavior when no `--ai` is provided — auto-detection preserved

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small CLI type change + conditional logic
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 5)
  - **Blocks**: None
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `src/ghaiw/cli/work.py:76` — current `--ai` flag definition: `ai: str | None = typer.Option(None, "--ai", ...)`. Change type to `list[str] | None`
  - `src/ghaiw/cli/work.py:80-120` — `start()` command handler where `ai` value is used. The tool resolution logic starts around here
  - `src/ghaiw/ui/prompts.py` — `select()` function for interactive selection when multiple tools given

  **Bash Reference**:
  - `lib/work/sync.sh:38-52` — Bash accepts multiple `--ai` flags, builds array, then shows selection menu if >1

  **Test References**:
  - `tests/unit/test_cli/` — CLI test directory. Check if it exists; create with `__init__.py` if not

  **WHY Each Reference Matters**:
  - `cli/work.py:76` — the exact flag definition to change
  - `ui/prompts.py:select()` — the existing interactive selection function to call for multi-tool choice
  - Bash reference shows the expected UX: present a numbered list, user picks one

  **Acceptance Criteria**:
  - [ ] Test file created: `tests/unit/test_cli/test_work_ai_flags.py`
  - [ ] `uv run pytest tests/unit/test_cli/test_work_ai_flags.py -v` → PASS (4 tests, 0 failures)
  - [ ] `--ai` flag accepts multiple values
  - [ ] Single `--ai` behavior unchanged
  - [ ] No `--ai` behavior unchanged

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Multiple --ai flags trigger selection
    Tool: Bash (uv run pytest)
    Preconditions: Test mocks ui.prompts.select() to return "claude"
    Steps:
      1. Run `uv run pytest tests/unit/test_cli/test_work_ai_flags.py::test_multiple_ai_shows_selection -v`
      2. Assert PASSED
      3. Verify test asserts select() was called with ["claude", "copilot"] choices
    Expected Result: Selection prompt invoked with correct tool list
    Failure Indicators: select() not called, or called with wrong args
    Evidence: .sisyphus/evidence/task-4-multi-ai-selection.txt

  Scenario: Single --ai flag passes through unchanged
    Tool: Bash (uv run pytest)
    Preconditions: No mocking of select()
    Steps:
      1. Run `uv run pytest tests/unit/test_cli/test_work_ai_flags.py::test_single_ai_flag_works_unchanged -v`
      2. Assert PASSED
    Expected Result: Tool used directly without selection prompt
    Failure Indicators: select() called unexpectedly
    Evidence: .sisyphus/evidence/task-4-single-ai-passthrough.txt
  ```

  **Commit**: YES
  - Message: `fix(cli): accept multiple --ai flags with interactive selection`
  - Files: `src/ghaiw/cli/work.py`, `tests/unit/test_cli/test_work_ai_flags.py`
  - Pre-commit: `uv run pytest tests/unit/test_cli/test_work_ai_flags.py -v`

- [ ] 5. BUG-005: Make shell-init output bash/zsh/fish syntax

  **What to do**:
  - **RED**: Create test file `tests/unit/test_cli/test_shell_init.py`
  - Write test `test_bash_output_default()`: When `$SHELL` is `/bin/bash` or unset, output contains bash function syntax with `[[ ]]`
  - Write test `test_zsh_output()`: When `$SHELL` is `/bin/zsh`, output contains zsh-compatible function syntax
  - Write test `test_fish_output()`: When `$SHELL` is `/usr/bin/fish`, output contains `function ghaiwpy ... end` Fish syntax
  - Write test `test_shell_flag_overrides_env()`: When `--shell fish` passed, output is Fish regardless of `$SHELL`
  - Write test `test_bash_output_unchanged()`: The Bash output must match current behavior exactly (no regression)
  - Run tests → expect zsh/fish tests FAIL (RED)
  - **GREEN**: In `src/ghaiw/cli/admin.py:87-105`:
    1. Add `--shell` option: `shell: str | None = typer.Option(None, "--shell", help="Target shell: bash, zsh, fish")`
    2. Detect shell: `detected = shell or os.environ.get("SHELL", "/bin/bash")`
    3. Map detected shell to output generator:
       - `/bin/bash` or `/bin/zsh` or anything with `bash`/`zsh` → bash/zsh function (current output — `[[ ]]` works in both)
       - `/usr/bin/fish` or anything with `fish` → Fish function syntax: `function ghaiwpy ... end`
    4. Fish syntax must handle `ghaiwpy work cd` interception like bash version but using Fish idioms: `set -l`, `builtin cd`, `eval`
  - Run tests → expect PASS (GREEN)

  **Must NOT do**:
  - MUST NOT break existing Bash output — zsh/fish support is additive
  - MUST NOT add PowerShell support — bash/zsh/fish only
  - Note: `[[ ]]` IS compatible with Zsh, so Bash and Zsh can share the same output

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multi-shell output requires understanding Fish function syntax (different from bash/zsh)
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3, 4)
  - **Blocks**: None
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `src/ghaiw/cli/admin.py:87-105` — current `shell_init()` function. Outputs a hardcoded bash function string. This is the function to modify
  - `src/ghaiw/ui/prompts.py` — NOT needed for this task (shell-init outputs text, doesn't prompt)

  **Bash Reference**:
  - Bash ghaiw had multi-shell in older versions. The key difference is Fish's function syntax:
    ```fish
    function ghaiwpy
      if test (count $argv) -ge 2; and test "$argv[1]" = "work"; and test "$argv[2]" = "cd"
        set -l dir (command ghaiwpy work cd $argv[3..])
        and builtin cd $dir
      else
        command ghaiwpy $argv
      end
    end
    ```

  **Test References**:
  - `tests/unit/test_cli/` — CLI test directory

  **WHY Each Reference Matters**:
  - `admin.py:87-105` — the exact function to modify. Contains the current bash-only output
  - Fish syntax reference — Fish uses `function/end` blocks, `test` instead of `[[ ]]`, `$argv` instead of `$@`, `set -l` instead of `local`

  **Acceptance Criteria**:
  - [ ] Test file created: `tests/unit/test_cli/test_shell_init.py`
  - [ ] `uv run pytest tests/unit/test_cli/test_shell_init.py -v` → PASS (5 tests, 0 failures)
  - [ ] `--shell` flag added to `shell-init` command
  - [ ] Bash output unchanged from current behavior
  - [ ] Fish output uses `function ... end` syntax

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Bash output matches current behavior
    Tool: Bash (uv run pytest)
    Preconditions: SHELL env set to /bin/bash
    Steps:
      1. Run `uv run pytest tests/unit/test_cli/test_shell_init.py::test_bash_output_unchanged -v`
      2. Assert PASSED
      3. Test verifies output contains `function ghaiwpy()`, `[[ ]]`, `local`
    Expected Result: Bash function syntax unchanged
    Failure Indicators: Missing bash keywords or changed function structure
    Evidence: .sisyphus/evidence/task-5-bash-output.txt

  Scenario: Fish output uses Fish-native syntax
    Tool: Bash (uv run pytest)
    Preconditions: SHELL env set to /usr/bin/fish (or --shell fish)
    Steps:
      1. Run `uv run pytest tests/unit/test_cli/test_shell_init.py::test_fish_output -v`
      2. Assert PASSED
      3. Test verifies output contains `function ghaiwpy`, `end`, `set -l`, `test`
    Expected Result: Fish-native function syntax
    Failure Indicators: Bash syntax in output (e.g., `[[ ]]`, `local`)
    Evidence: .sisyphus/evidence/task-5-fish-output.txt

  Scenario: --shell flag overrides SHELL env
    Tool: Bash (uv run pytest)
    Preconditions: SHELL=/bin/bash but --shell fish passed
    Steps:
      1. Run `uv run pytest tests/unit/test_cli/test_shell_init.py::test_shell_flag_overrides_env -v`
      2. Assert PASSED
    Expected Result: Fish syntax output despite SHELL=/bin/bash
    Failure Indicators: Bash syntax in output
    Evidence: .sisyphus/evidence/task-5-shell-flag-override.txt
  ```

  **Commit**: YES
  - Message: `fix(cli): make shell-init output bash/zsh/fish syntax based on detected shell`
  - Files: `src/ghaiw/cli/admin.py`, `tests/unit/test_cli/test_shell_init.py`
  - Pre-commit: `uv run pytest tests/unit/test_cli/test_shell_init.py -v`

- [ ] 6. BUG-004: Add iTerm2 AppleScript launch for --detach mode

  **What to do**:
  - **RED**: Create test file `tests/unit/test_utils/test_terminal_launch.py`
  - Write test `test_iterm2_detected()`: When `TERM_PROGRAM=iTerm.app`, `detect_terminal()` returns `"iterm2"` (already works — verify)
  - Write test `test_iterm2_launch_command()`: When terminal is `"iterm2"` on macOS, `launch_in_new_terminal()` calls `osascript` with iTerm2 AppleScript: `tell application "iTerm2" to create window with default profile command "cd '{cwd}' && {cmd}"`
  - Write test `test_iterm2_launch_before_terminal_app()`: iTerm2 is tried BEFORE Terminal.app fallback
  - Run tests → expect iterm2 launch tests FAIL (RED)
  - **GREEN**: In `src/ghaiw/utils/terminal.py`, add iTerm2 launch block between tmux and Terminal.app sections (around line 147):
    1. Check `if terminal == "iterm2" and sys.platform == "darwin":`
    2. Build AppleScript: `tell application "iTerm2" to create window with default profile command "cd '{cwd}' && {cmd_str}"`
    3. Execute via `subprocess.run(["osascript", "-e", osa_script], check=True, capture_output=True)`
    4. Return `True` on success, fall through on `CalledProcessError`
  - Run tests → expect PASS (GREEN)

  **Must NOT do**:
  - MUST NOT test actual terminal launch — test COMMAND CONSTRUCTION only
  - MUST NOT change the terminal detection order in `detect_terminal()`
  - MUST NOT import platform-specific modules at module level

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small addition to existing launch function with clear Bash reference
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 8)
  - **Blocks**: None
  - **Blocked By**: None (can start immediately, but Wave 2 for organizational clarity)

  **References**:

  **Pattern References**:
  - `src/ghaiw/utils/terminal.py:108-162` — `launch_in_new_terminal()` function. iTerm2 block should be added between the tmux block (line 134-145) and the Terminal.app block (line 147-159)
  - `src/ghaiw/utils/terminal.py:95-96` — detection code for iTerm2 (already works: `if "iterm" in term_program: return "iterm2"`)
  - `src/ghaiw/utils/terminal.py:147-159` — Terminal.app AppleScript pattern to follow as a template for the iTerm2 block

  **Bash Reference**:
  - `lib/work/terminal.sh:67-69` — iTerm2 AppleScript: `osascript -e "tell application \"iTerm2\" to create window with default profile command \"cd '${working_dir}' && ${title_prefix}${cmd}\""` — simpler than Ghostty (no menu click needed), just creates a window with command

  **Test References**:
  - `tests/unit/test_utils/` — utility test directory. Create `test_terminal_launch.py` shared by Tasks 6, 7, 8

  **WHY Each Reference Matters**:
  - `terminal.py:108-162` — the function to modify. Shows existing launch pattern (try, catch CalledProcessError, return True/False)
  - `terminal.py:147-159` — Terminal.app block is the closest pattern to follow for iTerm2 (both use osascript)
  - `terminal.sh:67-69` — exact AppleScript command from Bash. iTerm2 uses `create window with default profile command` — much simpler than Ghostty's menu-click approach

  **Acceptance Criteria**:
  - [ ] Test file created: `tests/unit/test_utils/test_terminal_launch.py`
  - [ ] `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v -k iterm` → PASS (3 tests, 0 failures)
  - [ ] iTerm2 AppleScript launch code added to `launch_in_new_terminal()`
  - [ ] iTerm2 is tried before Terminal.app in the launch order

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: iTerm2 launch constructs correct AppleScript
    Tool: Bash (uv run pytest)
    Preconditions: subprocess.run mocked, sys.platform patched to "darwin", detect_terminal returns "iterm2"
    Steps:
      1. Run `uv run pytest tests/unit/test_utils/test_terminal_launch.py::test_iterm2_launch_command -v`
      2. Assert PASSED
      3. Verify the test asserts osascript was called with args containing 'tell application "iTerm2"' and 'create window with default profile command'
    Expected Result: osascript called with correct iTerm2 AppleScript
    Failure Indicators: osascript not called, or called with Terminal.app script instead
    Evidence: .sisyphus/evidence/task-6-iterm2-launch.txt

  Scenario: iTerm2 tried before Terminal.app
    Tool: Bash (uv run pytest)
    Preconditions: Both iTerm2 and Terminal.app available, subprocess.run mocked to succeed on first call
    Steps:
      1. Run `uv run pytest tests/unit/test_utils/test_terminal_launch.py::test_iterm2_launch_before_terminal_app -v`
      2. Assert PASSED
      3. Verify subprocess.run was called exactly once (iTerm2 succeeded, Terminal.app not reached)
    Expected Result: Only one osascript call (iTerm2), Terminal.app not attempted
    Failure Indicators: Two osascript calls, or Terminal.app called first
    Evidence: .sisyphus/evidence/task-6-iterm2-priority.txt
  ```

  **Commit**: YES (groups with T7, T8 — commit after all three terminal tasks)
  - Message: `fix(utils): add iTerm2 AppleScript launch for detach mode`
  - Files: `src/ghaiw/utils/terminal.py`, `tests/unit/test_utils/test_terminal_launch.py`
  - Pre-commit: `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v`

- [ ] 7. BUG-007: Add gnome-terminal detection and launch support

  **What to do**:
  - **RED**: In test file `tests/unit/test_utils/test_terminal_launch.py` (created by Task 6):
  - Write test `test_gnome_terminal_detected()`: When `shutil.which("gnome-terminal")` returns a path AND no other terminal detected, `detect_terminal()` returns `"gnome-terminal"`
  - Write test `test_gnome_terminal_launch_command()`: When gnome-terminal is available, `launch_in_new_terminal()` calls `subprocess.Popen(["gnome-terminal", "--", "bash", "-c", "cd '{cwd}' && {cmd}; exec bash"])`
  - Write test `test_gnome_terminal_not_on_macos()`: gnome-terminal is NOT tried on macOS (only Linux)
  - Run tests → expect FAIL (RED)
  - **GREEN**: In `src/ghaiw/utils/terminal.py`:
    1. In `detect_terminal()` (around line 105), add gnome-terminal detection:
       ```python
       if shutil.which("gnome-terminal"):
           return "gnome-terminal"
       ```
       This should come AFTER tmux detection but BEFORE returning `None`
    2. In `launch_in_new_terminal()`, add gnome-terminal block AFTER the macOS Terminal.app block (around line 160):
       ```python
       if terminal == "gnome-terminal" and shutil.which("gnome-terminal"):
           cmd_str = " ".join(command)
           try:
               subprocess.Popen(
                   ["gnome-terminal", "--", "bash", "-c", f"cd '{cwd or '.'}' && {cmd_str}; exec bash"],
                   start_new_session=True,
               )
               return True
           except OSError:
               pass
       ```
  - Run tests → expect PASS (GREEN)

  **Must NOT do**:
  - MUST NOT test actual terminal launch — test COMMAND CONSTRUCTION only
  - MUST NOT change `detect_terminal()` order for existing terminals (ghostty, iterm2, terminal.app, wezterm, tmux)
  - MUST NOT try gnome-terminal on macOS in the launch function

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small addition to existing detection + launch functions
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6, 8)
  - **Blocks**: None
  - **Blocked By**: None (can start immediately, but Wave 2 for organizational clarity)

  **References**:

  **Pattern References**:
  - `src/ghaiw/utils/terminal.py:87-105` — `detect_terminal()` function. gnome-terminal detection should be added after tmux (line 103) and before the final `return None` (line 105)
  - `src/ghaiw/utils/terminal.py:108-162` — `launch_in_new_terminal()`. gnome-terminal block goes after Terminal.app macOS block, before the final logger.warning

  **Bash Reference**:
  - `lib/work/terminal.sh:82-84` — gnome-terminal launch: `gnome-terminal -- bash -c "cd '${working_dir}' && ${title_prefix}${cmd}; exec bash"` — note the `; exec bash` to keep terminal open after command exits

  **Test References**:
  - `tests/unit/test_utils/test_terminal_launch.py` — shared test file created by Task 6

  **WHY Each Reference Matters**:
  - `terminal.py:87-105` — where to add detection. Must follow existing pattern: check env var or `which`, return string
  - `terminal.py:108-162` — where to add launch. Follow existing try/except pattern
  - `terminal.sh:82-84` — exact Bash command shows `--` separator, `bash -c`, and `; exec bash` pattern

  **Acceptance Criteria**:
  - [ ] gnome-terminal detection added to `detect_terminal()`
  - [ ] gnome-terminal launch added to `launch_in_new_terminal()`
  - [ ] `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v -k gnome` → PASS (3 tests, 0 failures)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: gnome-terminal detection works
    Tool: Bash (uv run pytest)
    Preconditions: TERM_PROGRAM unset or empty, shutil.which("gnome-terminal") mocked to return "/usr/bin/gnome-terminal"
    Steps:
      1. Run `uv run pytest tests/unit/test_utils/test_terminal_launch.py::test_gnome_terminal_detected -v`
      2. Assert PASSED
      3. Verify detect_terminal() returns "gnome-terminal"
    Expected Result: detect_terminal() returns "gnome-terminal"
    Failure Indicators: Returns None or wrong terminal name
    Evidence: .sisyphus/evidence/task-7-gnome-detected.txt

  Scenario: gnome-terminal launch uses correct command
    Tool: Bash (uv run pytest)
    Preconditions: subprocess.Popen mocked, detect_terminal returns "gnome-terminal"
    Steps:
      1. Run `uv run pytest tests/unit/test_utils/test_terminal_launch.py::test_gnome_terminal_launch_command -v`
      2. Assert PASSED
      3. Verify Popen called with ["gnome-terminal", "--", "bash", "-c", ...] and command string contains "; exec bash"
    Expected Result: Correct gnome-terminal command constructed
    Failure Indicators: Wrong args to Popen, missing "-- bash -c" or "; exec bash"
    Evidence: .sisyphus/evidence/task-7-gnome-launch.txt
  ```

  **Commit**: YES (groups with T6, T8 — commit after all three terminal tasks)
  - Message: `fix(utils): add gnome-terminal detection and launch support`
  - Files: `src/ghaiw/utils/terminal.py`, `tests/unit/test_utils/test_terminal_launch.py`
  - Pre-commit: `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v`

- [ ] 8. BUG-008: Fix Ghostty macOS to use AppleScript instead of subprocess

  **What to do**:
  - **RED**: In test file `tests/unit/test_utils/test_terminal_launch.py` (created by Task 6):
  - Write test `test_ghostty_macos_uses_applescript()`: When terminal is `"ghostty"` on macOS (`sys.platform == "darwin"`), `launch_in_new_terminal()` calls `osascript` with Ghostty-specific AppleScript (menu click + keystroke via temp script), NOT `subprocess.Popen(["ghostty", "-e", ...])`
  - Write test `test_ghostty_linux_uses_subprocess()`: When terminal is `"ghostty"` on Linux, uses `ghostty +new-window -e bash -c "..."` (current behavior is close but needs `+new-window`)
  - Write test `test_ghostty_macos_temp_script_created()`: On macOS, a temp launcher script is created with correct cd + exec content
  - Run tests → expect macOS tests FAIL (RED)
  - **GREEN**: In `src/ghaiw/utils/terminal.py`, replace the Ghostty launch block (lines 121-132):
    1. Add platform check: `if sys.platform == "darwin"`:
       - Create temp script file via `tempfile.NamedTemporaryFile(prefix="ghaiw-", dir="/tmp", suffix="", delete=False, mode="w")`
       - Write launcher content: `#!/usr/bin/env bash\ncd {cwd}\nexec {cmd_str}\n`
       - If title provided, add `printf '\\033]0;{title}\\007'` before exec
       - Make executable: `os.chmod(tmp_path, 0o755)`
       - Execute AppleScript via osascript:
         ```
         tell application "Ghostty" to activate
         delay 0.3
         tell application "System Events"
           tell process "Ghostty"
             tell menu bar 1
               tell menu bar item "File"
                 tell menu "File"
                   click menu item "New Tab"
                 end tell
               end tell
             end tell
           end tell
         end tell
         delay 1.0
         keystroke "{tmp_path}"
         key code 36
         end tell
         ```
       - Schedule cleanup: `threading.Timer(15, lambda: os.unlink(tmp_path)).start()`
       - On `CalledProcessError`: fall back to `open -na "Ghostty" --args --working-directory={cwd} -e bash -c "{cmd_str}"`
    2. `else` (Linux): Change from `["ghostty", "-e", cmd_str]` to use `+new-window` flag:
       ```python
       ghostty_bin = os.environ.get("GHOSTTY_BIN_DIR", "")
       ghostty_bin = f"{ghostty_bin}/ghostty" if ghostty_bin else "ghostty"
       subprocess.Popen([ghostty_bin, "+new-window", "-e", "bash", "-c", f"cd '{cwd or '.'}' && {cmd_str}"], ...)
       ```
  - Run tests → expect PASS (GREEN)

  **Must NOT do**:
  - MUST NOT test actual terminal launch — test COMMAND CONSTRUCTION only
  - MUST NOT test actual AppleScript execution — mock subprocess.run for osascript
  - MUST NOT change behavior on Linux beyond adding `+new-window` flag
  - Platform-gated tests: macOS tests skip on Linux, Linux tests skip on macOS (use `@pytest.mark.skipif`)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Ghostty launch pattern is well-defined from Bash reference, just needs translation
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 6, 7)
  - **Blocks**: None
  - **Blocked By**: None (can start immediately, but Wave 2 for organizational clarity)

  **References**:

  **Pattern References**:
  - `src/ghaiw/utils/terminal.py:121-132` — current Ghostty launch block. Uses `["ghostty", "-e", cmd_str]` on ALL platforms — needs platform split

  **Bash Reference**:
  - `lib/work/terminal.sh:20-64` — full Ghostty launch code. macOS path (lines 21-57): temp script + AppleScript menu click + keystroke + delayed cleanup. Linux path (lines 58-63): `ghostty +new-window -e bash -c "..."`
  - Key pattern: macOS Ghostty can't be controlled via simple CLI — requires AppleScript to click File > New Tab, then type the command path

  **Test References**:
  - `tests/unit/test_utils/test_terminal_launch.py` — shared test file created by Task 6

  **WHY Each Reference Matters**:
  - `terminal.py:121-132` — the exact block to replace. Current code is Linux-only approach on all platforms
  - `terminal.sh:20-64` — the Bash implementation to replicate. Shows WHY AppleScript is needed on macOS (no direct CLI control) and the temp-script workaround for quoting issues

  **Acceptance Criteria**:
  - [ ] Ghostty macOS launch uses AppleScript via osascript
  - [ ] Ghostty Linux launch uses `+new-window` flag
  - [ ] `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v -k ghostty` → PASS (3 tests, 0 failures)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Ghostty macOS uses AppleScript (not subprocess)
    Tool: Bash (uv run pytest)
    Preconditions: sys.platform patched to "darwin", detect_terminal returns "ghostty", subprocess.run and os.chmod mocked
    Steps:
      1. Run `uv run pytest tests/unit/test_utils/test_terminal_launch.py::test_ghostty_macos_uses_applescript -v`
      2. Assert PASSED
      3. Verify subprocess.run called with ["osascript", "-e", ...] containing 'tell application "Ghostty"'
      4. Verify ["ghostty", "-e"] was NOT called
    Expected Result: osascript used for Ghostty on macOS, not direct subprocess
    Failure Indicators: subprocess.Popen called with ["ghostty", "-e", ...]
    Evidence: .sisyphus/evidence/task-8-ghostty-macos-applescript.txt

  Scenario: Ghostty Linux uses +new-window flag
    Tool: Bash (uv run pytest)
    Preconditions: sys.platform patched to "linux", detect_terminal returns "ghostty"
    Steps:
      1. Run `uv run pytest tests/unit/test_utils/test_terminal_launch.py::test_ghostty_linux_uses_subprocess -v`
      2. Assert PASSED
      3. Verify subprocess.Popen called with args containing "+new-window"
    Expected Result: Ghostty called with +new-window flag on Linux
    Failure Indicators: Missing +new-window, or osascript called on Linux
    Evidence: .sisyphus/evidence/task-8-ghostty-linux-newwindow.txt
  ```

  **Commit**: YES (groups with T6, T7 — commit after all three terminal tasks)
  - Message: `fix(utils): use AppleScript for Ghostty macOS launch`
  - Files: `src/ghaiw/utils/terminal.py`, `tests/unit/test_utils/test_terminal_launch.py`
  - Pre-commit: `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v`

- [ ] 9. BUG-003: Implement post-work lifecycle prompt after AI tool exits

  **What to do**:
  - **RED**: Create test file `tests/unit/test_services/test_post_work_lifecycle.py`
  - Write test `test_pr_strategy_prompts_merge_on_existing_pr()`: When PR exists for branch, user is prompted "Do you want to merge this PR?" (Y default). Mock confirms → verifies `gh pr merge --squash --delete-branch` called
  - Write test `test_pr_strategy_no_pr_warns_and_returns()`: When no PR exists, prints warning and returns without prompting
  - Write test `test_pr_strategy_user_declines_merge()`: When user says N → no merge attempted, function returns cleanly
  - Write test `test_pr_strategy_merge_failure_handles_gracefully()`: When `gh pr merge` fails → prints error, attempts remote branch delete, does NOT raise
  - Write test `test_pr_strategy_cleanup_and_pull_after_merge()`: After successful merge → cleanup worktree + `git pull --quiet` + close issue if number provided
  - Write test `test_direct_strategy_zero_ahead_offers_delete()`: When branch has 0 commits ahead of main → offers to delete empty worktree
  - Write test `test_direct_strategy_commits_ahead_shows_menu()`: When branch has N commits ahead → shows menu: merge / merge+close / skip
  - Write test `test_direct_strategy_merge_and_close()`: When user picks "merge+close" → merge + cleanup + close issue
  - Write test `test_lifecycle_skipped_in_detach_mode()`: Function not called when `--detach` is True
  - Write test `test_lifecycle_runs_after_ai_crash()`: Even if AI tool exits with non-zero, lifecycle still runs (it's in a finally/always block)
  - Run tests → expect ALL FAIL (RED)
  - **GREEN**: In `src/ghaiw/services/work_service.py`:
    1. Create standalone function `_post_work_lifecycle(repo_root: Path, branch: str, issue_number: int | None, config: ProjectConfig, provider: AbstractTaskProvider) -> None`
    2. Place it near `_cleanup_worktree()` (around line 1500+) for locality
    3. **PR strategy** implementation (when `config.project.merge_strategy == MergeStrategy.PR`):
       a. Resolve worktree path from `git worktree list` (reuse existing `_find_worktree_for_branch()` or similar)
       b. Check if PR exists: call `provider.get_pr_for_branch(branch)` or `git_pr.get_pr_number_for_branch()`
       c. If no PR → `console.warning("No open PR found for branch '{branch}'. Skipping lifecycle.")` → return
       d. If PR exists → `console.confirm("Do you want to merge this PR? (#{pr_number})", default=True)`
       e. If user confirms → call `git_pr.merge_pr(pr_number, squash=True, delete_branch=True)`
       f. On merge failure → log error, attempt `git_pr.delete_remote_branch(branch)` as best-effort, do NOT raise
       g. On success → `_cleanup_worktree(worktree_path)` → `subprocess.run(["git", "pull", "--quiet"])` in repo_root
       h. If `issue_number` → `provider.close_task(issue_number)`
       i. Add comment: `# NOTE: auto-version bump intentionally omitted — no Python equivalent exists`
    4. **Direct strategy** implementation (when `config.project.merge_strategy == MergeStrategy.DIRECT`):
       a. Count commits ahead of main: `git rev-list --count main..{branch}`
       b. If 0 ahead → `console.confirm("Branch has no new commits. Delete empty worktree?", default=False)`
       c. If >0 ahead → show menu via `ui_prompts.select()` with choices: `["Merge into main", "Merge + close task", "Skip"]`
       d. For merge options → call existing `_worktree_merge_and_cleanup()` or compose from primitives: `git merge`, `_cleanup_worktree()`, `git push`
       e. For "Merge + close" → also call `provider.close_task(issue_number)` if issue_number provided
       f. For "Skip" → return without action
    5. **Call site**: In `start()` method, after the AI tool exits (around line 454), add:
       ```python
       # Post-work lifecycle — only for inline mode (not --detach)
       if not detach:
           try:
               _post_work_lifecycle(repo_root, branch, issue_number, config, provider)
           except Exception:
               logger.exception("post_work_lifecycle.failed")
       ```
       This must be in a try/except so lifecycle failures don't mask the AI exit
  - Run tests → expect ALL PASS (GREEN)
  - **REFACTOR**: Extract PR merge retry logic if it's duplicated. Ensure all error paths log clearly

  **Must NOT do**:
  - MUST NOT duplicate `done()` logic — call existing primitives only
  - MUST NOT create PRs — only detect and merge existing ones
  - MUST NOT add auto-version bump — add comment noting intentional omission
  - MUST NOT implement lifecycle for `--detach` mode — only inline mode
  - MUST NOT modify `done()` function itself
  - MUST NOT use `mock_gh` fixture — use `@patch` decorators for all subprocess/provider mocking
  - MUST NOT call lifecycle from `done()` — it's called from `start()` after AI exits

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Largest task — multi-strategy lifecycle with error handling, multiple user prompts, and integration with existing primitives
  - **Skills**: `[]`
    - No special skills needed — all internal code
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not needed — no browser interaction

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (sequential — depends on Task 3)
  - **Blocks**: F1-F4 (Final Verification)
  - **Blocked By**: Task 3 (BUG-006) — PR-SUMMARY.md path resolution must be done first since lifecycle references similar path patterns

  **References**:

  **Pattern References**:
  - `src/ghaiw/services/work_service.py:439-465` — `start()` method's AI launch section. The lifecycle call should go AFTER line 454 (where AI tool returns), inside the `if not detach` block
  - `src/ghaiw/services/work_service.py:1500-1537` — `_cleanup_worktree()` function. Call this for worktree cleanup, do not reimplement
  - `src/ghaiw/services/work_service.py:1147-1200` — `_done_via_pr()` for reference on how PR operations are structured. Do NOT call this function — use primitives directly
  - `src/ghaiw/services/work_service.py:1270-1310` — `_done_via_direct()` for reference on direct merge pattern
  - `src/ghaiw/git/pr.py` — PR operations: `get_pr_number_for_branch()`, `merge_pr()`, `delete_remote_branch()`
  - `src/ghaiw/providers/github.py` — `get_pr_for_branch()`, `close_task()`
  - `src/ghaiw/ui/prompts.py` — `confirm()` for Y/N prompts, `select()` for menu selection

  **Bash Reference**:
  - `lib/worktree.sh:122-268` — `_worktree_post_work_lifecycle()`. Full implementation:
    - Lines 135-210: PR strategy (check PR, prompt merge, squash-merge, cleanup, pull, close issue)
    - Lines 212-267: Direct strategy (count ahead, empty worktree prompt, merge/close menu)
  - `lib/work/terminal.sh:203-210` — Call site: lifecycle is called after BOTH terminal and GUI tool exit, in inline mode only

  **Test References**:
  - `tests/unit/test_services/` — service test files showing mock patterns with `@patch`
  - `tests/conftest.py` — `tmp_git_repo` and `tmp_ghaiw_project` fixtures for git repo setup

  **WHY Each Reference Matters**:
  - `work_service.py:439-465` — the CALL SITE. Must understand the control flow to insert lifecycle correctly
  - `work_service.py:1500-1537` — `_cleanup_worktree()` is the ONLY way to clean worktrees. Must call it, not reimplement
  - `worktree.sh:122-268` — the SPECIFICATION. This is what we're implementing. Every branch of logic in Bash must have a Python equivalent
  - `ui/prompts.py` — the user interaction functions. `confirm()` for Y/N, `select()` for menus. Must use these, not `input()`
  - `git/pr.py` — PR primitives. Must check which functions exist and use them directly

  **Acceptance Criteria**:
  - [ ] Test file created: `tests/unit/test_services/test_post_work_lifecycle.py`
  - [ ] `uv run pytest tests/unit/test_services/test_post_work_lifecycle.py -v` → PASS (10 tests, 0 failures)
  - [ ] `_post_work_lifecycle()` function exists in `work_service.py`
  - [ ] PR strategy: detect PR → prompt merge → squash-merge → cleanup → pull → close issue
  - [ ] Direct strategy: count ahead → menu (merge/merge+close/skip) → execute choice
  - [ ] Lifecycle called from `start()` after AI exits, only in inline mode
  - [ ] Auto-version bump comment present in source
  - [ ] All error paths handled (no unhandled exceptions from lifecycle)

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: PR strategy — user merges existing PR
    Tool: Bash (uv run pytest)
    Preconditions: Mocked provider returns PR #99 for branch, confirm() mocked to return True, merge_pr mocked to succeed
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_post_work_lifecycle.py::test_pr_strategy_prompts_merge_on_existing_pr -v`
      2. Assert PASSED
      3. Verify confirm() called with message containing "merge" and "#99"
      4. Verify merge_pr called with squash=True, delete_branch=True
      5. Verify _cleanup_worktree called
      6. Verify git pull --quiet called
    Expected Result: Full merge lifecycle completed
    Failure Indicators: Any mock not called, or called with wrong args
    Evidence: .sisyphus/evidence/task-9-pr-merge.txt

  Scenario: PR strategy — no PR found, warns and returns
    Tool: Bash (uv run pytest)
    Preconditions: Mocked provider returns None for branch PR lookup
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_post_work_lifecycle.py::test_pr_strategy_no_pr_warns_and_returns -v`
      2. Assert PASSED
      3. Verify confirm() was NOT called
      4. Verify merge_pr was NOT called
    Expected Result: Warning logged, no merge attempted
    Failure Indicators: confirm() called despite no PR
    Evidence: .sisyphus/evidence/task-9-no-pr.txt

  Scenario: PR strategy — merge fails gracefully
    Tool: Bash (uv run pytest)
    Preconditions: merge_pr mocked to raise CalledProcessError
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_post_work_lifecycle.py::test_pr_strategy_merge_failure_handles_gracefully -v`
      2. Assert PASSED
      3. Verify error logged (not raised)
      4. Verify delete_remote_branch called as best-effort
    Expected Result: Error handled gracefully, no exception propagated
    Failure Indicators: Exception raised from lifecycle, or delete_remote_branch not attempted
    Evidence: .sisyphus/evidence/task-9-merge-failure.txt

  Scenario: Direct strategy — commits ahead shows menu
    Tool: Bash (uv run pytest)
    Preconditions: git rev-list returns "3" (3 commits ahead), select() mocked to return "Merge + close task"
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_post_work_lifecycle.py::test_direct_strategy_commits_ahead_shows_menu -v`
      2. Assert PASSED
      3. Verify select() called with choices containing "Merge", "Merge + close", "Skip"
    Expected Result: Menu shown with correct options
    Failure Indicators: select() not called or called with wrong choices
    Evidence: .sisyphus/evidence/task-9-direct-menu.txt

  Scenario: Lifecycle skipped in detach mode
    Tool: Bash (uv run pytest)
    Preconditions: detach=True in start() call
    Steps:
      1. Run `uv run pytest tests/unit/test_services/test_post_work_lifecycle.py::test_lifecycle_skipped_in_detach_mode -v`
      2. Assert PASSED
      3. Verify _post_work_lifecycle was NOT called
    Expected Result: No lifecycle prompt in detach mode
    Failure Indicators: _post_work_lifecycle called despite detach=True
    Evidence: .sisyphus/evidence/task-9-detach-skip.txt
  ```

  **Evidence to Capture:**
  - [ ] Each evidence file named: task-9-{scenario-slug}.txt
  - [ ] Terminal output for each test run

  **Commit**: YES
  - Message: `fix(work): implement post-work lifecycle prompt after AI tool exits`
  - Files: `src/ghaiw/services/work_service.py`, `tests/unit/test_services/test_post_work_lifecycle.py`
  - Pre-commit: `uv run pytest tests/unit/test_services/test_post_work_lifecycle.py -v`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run test). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in `.sisyphus/evidence/`. Compare deliverables against plan.
  Output: `Must Have [9/9] | Must NOT Have [9/9] | Tasks [9/9] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `uv run mypy src/ --strict` + `uv run ruff check src/` + `uv run pytest tests/ -v --ignore=tests/live`. Review all changed files for: `as any`/type: ignore, empty catches, print() in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Mypy [PASS/FAIL] | Ruff [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high`
  Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git log/diff). Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Flag unaccounted changes.
  Output: `Tasks [9/9 compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Task | Commit Message | Files | Pre-commit Check |
|------|---------------|-------|-----------------|
| T1 | `fix(ai): correct copilot model probing regex to use non-capturing group` | `ai_tools/model_utils.py`, test file | `uv run pytest tests/unit/test_ai_tools/test_model_probing.py -v` |
| T2 | `fix(models): implement connected-component partitioning in DependencyGraph` | `models/deps.py`, test file | `uv run pytest tests/unit/test_models/test_deps_partition.py -v` |
| T3 | `fix(work): check worktree first for PR-SUMMARY.md before /tmp fallback` | `services/work_service.py`, test file | `uv run pytest tests/unit/test_services/test_pr_summary_path.py -v` |
| T4 | `fix(cli): accept multiple --ai flags with interactive selection` | `cli/work.py`, test file | `uv run pytest tests/unit/test_cli/test_work_ai_flags.py -v` |
| T5 | `fix(cli): make shell-init output bash/zsh/fish syntax based on detected shell` | `cli/admin.py`, test file | `uv run pytest tests/unit/test_cli/test_shell_init.py -v` |
| T6 | `fix(utils): add iTerm2 AppleScript launch for detach mode` | `utils/terminal.py`, test file | `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v` |
| T7 | `fix(utils): add gnome-terminal detection and launch support` | `utils/terminal.py`, test file | `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v` |
| T8 | `fix(utils): use AppleScript for Ghostty macOS launch` | `utils/terminal.py`, test file | `uv run pytest tests/unit/test_utils/test_terminal_launch.py -v` |
| T9 | `fix(work): implement post-work lifecycle prompt after AI tool exits` | `services/work_service.py`, `ui/prompts.py` (if needed), test file | `uv run pytest tests/unit/test_services/test_post_work_lifecycle.py -v` |

---

## Success Criteria

### Verification Commands
```bash
uv run pytest tests/ -v --ignore=tests/live   # Expected: ALL PASS, 0 failures
uv run mypy src/ --strict                      # Expected: Success, 0 errors
uv run ruff check src/                         # Expected: 0 errors
uv run ruff format --check src/                # Expected: 0 reformatted
```

### Final Checklist
- [ ] All 9 "Must Have" bug fixes present
- [ ] All 9 "Must NOT Have" guardrails respected
- [ ] All tests pass (existing + new)
- [ ] Mypy strict passes
- [ ] Ruff passes
- [ ] Each bug has TDD test that fails before fix, passes after
