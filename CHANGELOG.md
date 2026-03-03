# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Conventional Commits](https://conventionalcommits.org/).

## [v1.1.5] — 2026-03-03

### Bug Fixes

- only show tool-specific config for tools actually in use (0ec030d)

### Chores

- remove .sisyphus planning artifacts (1747b14)

## [v1.1.4] — 2026-03-03

### Bug Fixes

- address 4 code review findings (safety, resilience, hardening, dead code) (555faf4)

## [v1.1.3] — 2026-03-03

### Bug Fixes

- address 7 bugs found in code review (531e7a6)

## [v1.1.2] — 2026-03-02

### Bug Fixes

- standardize session-scoped file creation and fix draft PR on empty branch (0b908ac)

## [v1.1.1] — 2026-03-02

### Bug Fixes

- suppress version banner for shell-init subcommand (7b0326e)

## [v1.1.0] — 2026-03-02

### Features

- auto-configure shell integration during wade init (05e0d53)

### Bug Fixes

- consolidate slugifiers and make cd_only redirect exception-safe (308d21e)

## [v1.0.13] — 2026-03-02

### Bug Fixes

- add --sandbox workspace-write to Codex trusted_dirs_args (683a067)

## [v1.0.12] — 2026-03-02

### Documentation

- rewrite README with user-facing value proposition and before/after workflow (1c82035)

## [v1.0.11] — 2026-03-02

### Chores

- rename project from ghaiw to WADE (Workflow for AI-Driven Engineering) (c156666)

## [v1.0.10] — 2026-03-02

### Bug Fixes

- harden PR workflow and config/deps validation (5963dc4)

## [v1.0.9] — 2026-03-01

### Bug Fixes

- harden terminal cleanup, summary stripping, init validation, and work start (2e5447c)

## [v1.0.8] — 2026-03-01

### Tests

- add regression tests for summary ordering in PR body updates (f3e18a8)

## [v1.0.7] — 2026-02-28

### Bug Fixes

- remove GHAIW_WORK_MODEL env var and fix summary ordering in PR body (a84957d)

## [v1.0.6] — 2026-02-28

### Bug Fixes

- address code review findings — config errors, worktree matching, merge safety, cleanup reporting, and more (9f8fa19)

## [v1.0.5] — 2026-02-28

### Bug Fixes

- address code review findings — worktree cleanup, error handling, layering, docs (b0b4627)

## [v1.0.4] — 2026-02-28

### Bug Fixes

- address code review findings across docs, types, tests, and scripts (9c97ebc)

## [v1.0.3] — 2026-02-28

### Bug Fixes

- prevent infinite re-exec loop in self-upgrade (777a061)

## [v1.0.2] — 2026-02-28

### Refactoring

- rework init wizard flow and section names (87abb47)

## [v1.0.1] — 2026-02-28

### Features

- frictionless install and seamless updates for open-source release (db6d710)

### Bug Fixes

- default init AI tool selects to "Skip" and shorten section names (be4682c)

### Documentation

- fix AI tool links and add missing tools (opencode, vscode, antigravity) (8541462)
- add links to AI tool homepages in README (dc00931)
- add gh CLI auth instructions for users and contributors (aaaef0a)
- rewrite README for users, move contributor content to CONTRIBUTING.md (c0f2360)

### Tests

- fix ruff E501/F811/I001/UP017 lint errors in test files (0053c13)

### Chores

- fix trailing whitespace in transcript fixtures (a6283d4)

## [v1.0.0] — 2026-02-28

### Features

- move plans to draft PRs and rename primary commands (9179146)
- add optional allowlist prompt in init and auto-propagate to worktrees (8d4995d)

### Bug Fixes

- use real transcripts in parser tests and fix regex bugs (6e4ed38)

### Documentation

- update all docs to use dev scripts instead of raw uv run calls (ed3cc76)
- Make prompt stronger about using auto_version.py (d8a7720)

### Refactoring

- normalize token usage blocks to consistent table format (551318c)

### Chores

- add dev scripts and update AGENTS.md to mandate their use (09ffacb)

## [v0.25.9] — 2026-02-27

### Bug Fixes

- Use maybe_configure_gemini_experimental in update as well (24cbf64)
- embed impl usage in issue body and use dot notation for model labels (012f3a9)
- Make Gemini experimental plan and Claude statusline optional (abc9f82)
- parse complexity from issue body and load default_model in config (3cba16e)
- improve plan-session exit warning to be clearer about not coding after plan mode (8b8793c)

### Other Changes

- Revert "chore: bump version to 0.25.9" (2754f0d)

## [v0.25.8] — 2026-02-27

### Bug Fixes

- remove launch command print, show prompt preview instead of full text (98b50ac)

## [v0.25.7] — 2026-02-27

### Features

- skip Gemini experimental.plan config if already set, prompt if not (484d1ae)

### Bug Fixes

- add blank line after version in interactive menu (ca70717)

## [v0.25.6] — 2026-02-27

### Bug Fixes

- remove blank line before version header in interactive menu (5c3096d)

## [v0.25.5] — 2026-02-27

### Features

- print version header on every subcommand invocation (dd3f561)

## [v0.25.4] — 2026-02-27

### Bug Fixes

- run model compatibility check before displaying model in plan session (1464793)

## [v0.25.3] — 2026-02-27

### Bug Fixes

- check model compatibility before launching plan and deps AI sessions (7fa684e)

## [v0.25.2] — 2026-02-27

### Bug Fixes

- place initial message before flags in launch command (a1b9896)

## [v0.25.1] — 2026-02-27

### Bug Fixes

- print launch command and fix flag-ordering for initial message (b642ee9)

## [v0.25.0] — 2026-02-27

### Features

- pass initial message via CLI instead of clipboard (22cc3aa)

### Documentation

- restructure AGENTS.md into progressive disclosure architecture (1c7f862)

### Chores

- rename ghaiw-py -> ghaiw across all files (00aa37f)

## [v0.24.6] — 2026-02-26

### Refactoring

- remove back functionality from init wizard and prompts (f528e06)

## [v0.24.5] — 2026-02-26

### Bug Fixes

- detect model from Claude transcripts and remove plan summary from PR body (1cfbdc0)

## [v0.24.4] — 2026-02-26

### Bug Fixes

- use sentinel-based back detection in select() to fix ← Back being treated as an option (a713c71)

## [v0.24.3] — 2026-02-26

### Bug Fixes

- use /tmp for all temp files instead of system default dir (3f7b491)

## [v0.24.2] — 2026-02-26

### Bug Fixes

- use /tmp directly for PR-SUMMARY fallback instead of tempfile.gettempdir() (ad532e6)

## [v0.24.1] — 2026-02-26

### Bug Fixes

- show prompt in panel matching work start layout (24c4ead)

## [v0.24.0] — 2026-02-26

### Features

- wire statusline setup into init flow (30d84cb)
- add optional Claude Code statusline setup step (938d65b)

## [v0.23.0] — 2026-02-26

### Features

- display version number on init startup (d466c23)

## [v0.22.3] — 2026-02-26

### Bug Fixes

- use last status bar match for correct Claude token counts (dbd13bc)

## [v0.22.2] — 2026-02-26

### Bug Fixes

- marker-based .gitignore management with reliable locate/replace/remove (7fcb366)

## [v0.22.1] — 2026-02-26

### Documentation

- add critical rules to work-context prompt; fix placeholder bug (e18acfb)

## [v0.22.0] — 2026-02-26

### Features

- print transcript file path before AI sessions (7730c23)

## [v0.21.0] — 2026-02-26

### Features

- show prompt preview and persist to file on task plan (5a17ad3)

## [v0.20.1] — 2026-02-26

### Bug Fixes

- install skill files into new worktrees during bootstrap (5d76233)

### Documentation

- reconcile plan-session prompt with SKILL.md; normalize PLAN.md casing (d709d10)

## [v0.20.0] — 2026-02-26

### Features

- add back navigation to ghaiwpy init interactive wizard (c68ccd3)

## [v0.19.3] — 2026-02-26

### Documentation

- strengthen PR-SUMMARY requirement in work-session skill (dd6be41)

## [v0.19.2] — 2026-02-26

### Bug Fixes

- fix 4 post-work bugs in lifecycle and usage capture (48b020a)

## [v0.19.1] — 2026-02-26

### Bug Fixes

- remove legacy .issue-context.md, gitignore PLAN.md (0dab45e)

## [v0.19.0] — 2026-02-26

### Features

- print clipboard prompt to console as fallback if clipboard missed (51b964d)

## [v0.18.0] — 2026-02-26

### Features

- show complexity/model as separate rows and add spacing in work start summary (d0f4827)

## [v0.17.0] — 2026-02-26

### Features

- pass trusted dirs to all AI tool launch sites (db6d528)
- replace issue number text inputs with interactive arrow-key selection menus (1900a20)

## [v0.16.5] — 2026-02-26

### Bug Fixes

- recognize ai.default_model as valid config key (aa7872d)

## [v0.16.4] — 2026-02-26

### Bug Fixes

- fix test isolation for task list and stale codex model ID (b19baa6)

## [v0.16.3] — 2026-02-26

### Refactoring

- replace monolithic workflow skill with phase-specific bundles (ea3679f)

## [v0.16.2] — 2026-02-25

### Refactoring

- strip premature migrations and fix model registry (4281f3d)

## [v0.16.1] — 2026-02-25

### Bug Fixes

- fix AI tool and model wiring in init and work start (426e0a2)

## [v0.16.0] — 2026-02-25

### Features

- use select menus for merge strategy and per-command model prompts (da2895f)

### Bug Fixes

- opencode headless execution and logging probe errors (abd1ee6)

## [v0.15.0] — 2026-02-25

### Features

- make confirm prompt require Enter key (97edf0f)

## [v0.14.2] — 2026-02-25

### Bug Fixes

- restore models.json shape after corrupted payload extraction test (b944b4e)

## [v0.14.1] — 2026-02-25

### Documentation

- update walkthrough.md (4010a46)

## [v0.14.0] — 2026-02-25

### Features

- native JSON schema structured outputs for Copilot and Claude adapters (50321e4)

## [v0.13.2] — 2026-02-25

### Documentation

- update walkthrough.md (2840f03)

## [v0.13.1] — 2026-02-25

### Bug Fixes

- robust JSON extraction in probe script (36a0e23)

## [v0.13.0] — 2026-02-25

### Features

- interactive AI auto-correction for model registry (4a959c3)

## [v0.12.0] — 2026-02-25

### Features

- implement local hardcoded model registry (b163b17)

### Bug Fixes

- wire work-context.md template into build_work_prompt, remove inline fallbacks (dbdef53)
- strip ANSI fragments from transcripts and warn on token extraction failure (c57dbe8)

## [v0.11.0] — 2026-02-25

### Features

- add confirmation prompt on work remove + modernize detail lines (048ba4f)

## [v0.10.1] — 2026-02-25

### Refactoring

- standardize on dot notation for all model IDs in config (a9226db)

## [v0.10.0] — 2026-02-25

### Features

- select-based model picker, fix deprecated models, update Gemini defaults (53048df)

## [v0.9.0] — 2026-02-25

### Features

- arrow-key navigation menus and modernized visual language (b90e6ba)

## [v0.8.0] — 2026-02-25

### Features

- modernize terminal UI with Rich panels, spinners, and semantic theme (6b569f1)
- add OpenCode as AI tool adapter (5dc64f5)

## [v0.7.0] — 2026-02-24

### Features

- wire deps-interactive.md template into interactive fallback (05a6758)

## [v0.6.0] — 2026-02-24

### Features

- add VS Code (code) as AI tool adapter (344ec81)

## [v0.5.10] — 2026-02-24

### Bug Fixes

- capture AI session transcript for plan token usage reporting (b000feb)
- use repo name as subdirectory for worktree paths (b3a359a)

### Refactoring

- unify HTML marker block utilities into markdown.py (b9eb952)

### Tests

- fix 5 CI failures in work service and CLI flag tests (7d6b4cb)

## [v0.5.9] — 2026-02-24

### Tests

- tighten 5 more weak assertions from third triage pass (62f0b59)

## [v0.5.8] — 2026-02-24

### Tests

- tighten remaining weak OR/broad-range assertions (d667523)

## [v0.5.7] — 2026-02-24

### Tests

- fix 5 remaining weak assertions from second triage pass (92a0125)

## [v0.5.6] — 2026-02-24

### Tests

- harden 9 test files from triage plan (75f7c3a)

## [v0.5.5] — 2026-02-24

### Bug Fixes

- remove --output-file flag from Claude CLI launch commands (4248856)

### Tests

- improve test coverage with 13 new tests and assertion hardening (5686fac)

## [v0.5.4] — 2026-02-24

### Bug Fixes

- resolve 5 Bash parity regressions identified in analysis (21a4b3f)

## [v0.5.3] — 2026-02-24

### Bug Fixes

- use mypy --strict to match local dev workflow (e6f5892)

## [v0.5.2] — 2026-02-24

### Bug Fixes

- fix CI failures in copilot probing and help text tests (ef061f0)

## [v0.5.1] — 2026-02-24

### Bug Fixes

- broaden type ignore for changelog import compatibility (1607439)

## [v0.5.0] — 2026-02-24

### Features

- wire changelog command to admin CLI (5b745e4)
- address Bash parity gaps in work sessions (77d7791)
- add get_dirty_status for detailed worktree diagnostics (492a223)

### Bug Fixes

- replace bare except handlers with structured logging (f35fc9a)
- match terminal title format to Bash convention (fe77417)

### Refactoring

- use NotImplementedError in abstract method bodies (d069ca4)

## [v0.4.0] — 2026-02-24

### Features

- add --plan flag to work done for worktree resolution by plan title (feef24e)
- add _resolve_worktree_from_plan() for --plan flag support (a908fec)

### Bug Fixes

- implement post-work lifecycle prompt after AI tool exits (ea129b7)
- use AppleScript for Ghostty macOS launch (3fa02c8)
- add gnome-terminal detection and launch support (6e50cb6)
- add iTerm2 AppleScript launch for detach mode (c0f3b12)
- accept multiple --ai flags with interactive selection (d041023)
- check worktree first for PR-SUMMARY.md before /tmp fallback (222ccbe)
- implement connected-component partitioning in DependencyGraph (2f024b5)
- correct copilot model probing regex to use non-capturing group (57c5ddf)
- parameterize remove_worktree force flag (80ccb7a)
- warn on duplicate AI tool TOOL_ID registration (5db94a8)
- set SQLite busy_timeout to 30s for parallel safety (c45aa31)
- make config migration pipeline atomic and validate AI tools (69e704a)
- add timeout and cleanup prompt to work service (335a7dc)
- log specific exceptions in get_pr_for_branch (5174d96)
- raise GitError on subprocess failure in get_conflicted_files (b8d715c)

### Tests

- add branch slugify tests (696ed73)

## [v0.3.4] — 2026-02-24

### Bug Fixes

- create CLAUDE.md symlink and correct skill/cross-tool symlink paths (65412f2)

## [v0.3.3] — 2026-02-24

### Bug Fixes

- resolve 5 mypy strict errors and add mypy to pre-commit (ce7813b)

## [v0.3.2] — 2026-02-24

### Bug Fixes

- normalize model format in build_launch_command for all tools (7c58685)

### Tests

- fix tautological assertions and add command assembly tests (a2733d0)

## [v0.3.1] — 2026-02-23

### Bug Fixes

- add --add-dir and --permission-mode to plan AI launch (e56fc49)

## [v0.3.0] — 2026-02-23

### Features

- add interactive main menu when ghaiwpy is called with no args (005af4c)

## [v0.2.2] — 2026-02-23

### Bug Fixes

- backfill every missing model tier individually, not all-or-nothing (3f64ee5)

## [v0.2.1] — 2026-02-23

### Bug Fixes

- resolve init UX issues — model defaults, command override hang, venv prompt (6212dda)

## [v0.2.0] — 2026-02-23

### Features

- add pre-push hook for auto version bumping on push to main (29f6cd4)
- close Bash parity gaps — transcript capture, AI guard, interactive menus, pickers (bddd4d4)
- implement Bash feature parity — model discovery, safety guard, plan mode, terminal title (3ac1392)

### Bug Fixes

- package templates in wheel and suppress structlog noise (3901580)
- let uv manage Python in install.sh instead of requiring system python3 (246a6f5)
- rename all user-facing CLI references from ghaiw to ghaiwpy (5ec1147)
- resolve all ruff lint and format errors, update pre-commit (86af0f3)
- resolve all ruff lint and mypy strict errors (e34d587)

### Refactoring

- rewrite E2E tests as subprocess-based CLI tests (11175e4)

### Tests

- add E2E test suite and smoke scripts (45e5485)
