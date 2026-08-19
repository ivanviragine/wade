# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Conventional Commits](https://conventionalcommits.org/).

## [v0.50.2] — 2026-08-19

### Bug Fixes

- list uncommitted files before the PR-merge "work will be lost" prompt (#454) (932c645)

## [v0.50.1] — 2026-08-18

### Chores

- bump crossby to v0.24.3 (#452) (403daa8)

## [v0.50.0] — 2026-08-18

### Features

- harden and align review-pass budget and skip guidance across workflows (#451) (cef9d99)

## [v0.49.1] — 2026-08-18

### Bug Fixes

- don't report review bots done until they've actually reviewed (#449) (01a3192)

## [v0.49.0] — 2026-08-18

### Features

- standardize WADE session summaries with emoji status and decisive dialog prompts (#444) (4c4d730)

## [v0.48.5] — 2026-08-18

### Documentation

- align installed agent guidance with the plan-driven lifecycle (#442) (d5dbcfc)

## [v0.48.4] — 2026-08-18

### Bug Fixes

- wrap long choices in interactive pickers instead of cropping (#446) (6b162b6)

## [v0.48.3] — 2026-08-18

### Documentation

- reconcile developer references with the current codebase (#440) (c20a564)

## [v0.48.2] — 2026-08-17

### Documentation

- refresh public onboarding and product positioning (#438) (3f822e2)

## [v0.48.1] — 2026-08-17

### Documentation

- tighten session-skill communication — terse reporting and unambiguous exit/complete signaling (#436) (0384f72)

## [v0.48.0] — 2026-08-17

### Features

- add config-driven bot-review trigger command with optional auto-trigger (#432) (fc2ff0d)

## [v0.47.0] — 2026-08-17

### Features

- adopt crossby 0.24.0 refreshed AI-tool model catalogs (#434) (3842332)

## [v0.46.1] — 2026-08-16

### Bug Fixes

- propagate Codex worktree launch context through sessions (#429) (032daa2)

## [v0.45.10] — 2026-08-16

### Bug Fixes

- resolve branch by issue number so retitled issues resume their PR/plan (#428) (c840b5e)

## [v0.45.9] — 2026-08-16

### Bug Fixes

- apply resolved autonomy mode to resumed implementation sessions (#427) (30cc8c9)

## [v0.45.8] — 2026-08-16

### Chores

- default implementation sessions to yolo, matching plan/review_batch (#424) (43054af)

## [v0.45.7] — 2026-08-16

### Bug Fixes

- preserve user-set ai.implement keys on re-init (#425) (71a25db)

## [v0.45.6] — 2026-08-16

### Bug Fixes

- forward resolved permission mode to child sessions when explicit (#426) (fbdd9c9)

## [v0.45.5] — 2026-08-16

### Bug Fixes

- drop done.require_review: false bypass hint from done review-refusal output (#422) (5424a00)

## [v0.45.4] — 2026-08-15

### Bug Fixes

- _pull_main_after_merge untracked-collision backup is never restored on retry failure (#420) (de59eb7)

## [v0.45.3] — 2026-08-14

### Bug Fixes

- resolve worktree/branch by issue number + project-scope UX (#417) (6158a41)

## [v0.45.2] — 2026-08-13

### Refactoring

- shared typed worktree model for list_worktrees() consumers (#416) (69931c7)

## [v0.45.1] — 2026-08-13

### Bug Fixes

- merge_pr hard-fails on recoverable "Head branch is out of date" (stale PR head sync) (#415) (d81a658)

## [v0.45.0] — 2026-08-13

### Features

- support a configurable base branch for plans and implementation (#377) (7bbde95)

## [v0.44.3] — 2026-08-12

### Refactoring

- move deterministic git-hook logic out of skills.installer (service-layer boundary) (#412) (d93468d)

## [v0.44.2] — 2026-08-12

### Bug Fixes

- allow writes to system temp dirs through worktree/plan write-guards (#410) (81fcf74)

## [v0.44.1] — 2026-08-12

### Bug Fixes

- wade implement startup catchup silently aborts on knowledge-store-migration untracked_conflict, leaving the session on a stale base (#408) (9968583)

## [v0.44.0] — 2026-08-12

### Features

- make WADE session output report-by-exception (terse on success) (#402) (9f96c94)

## [v0.43.0] — 2026-08-11

### Features

- allow AI-tool memory writes through worktree/plan write-guards (#388) (338a31c)

## [v0.42.6] — 2026-08-11

### Bug Fixes

- always surface the resolved permission mode and honor it in review sessions (#380) (01beb8a)

## [v0.42.5] — 2026-08-11

### Bug Fixes

- a timed-out headless review loses everything and is likely to time out again (#406) (1050ff9)

## [v0.42.4] — 2026-08-10

### Bug Fixes

- restore nested-session guard for Antigravity CLI in wade implement (#405) (8d16c06)

## [v0.42.3] — 2026-08-10

### Bug Fixes

- don't declare review complete when bot hasn't reviewed the latest commit (#404) (f36ed80)

## [v0.42.2] — 2026-08-10

### Bug Fixes

- `done` cannot tell a clean review from one that never ran (#400) (8788d52)

## [v0.42.1] — 2026-08-10

### Chores

- remove dead pre-push install wrappers superseded by the batch git-hook API (#399) (e39517a)

## [v0.42.0] — 2026-08-09

### Features

- enforce conventional-commit PR & issue titles across creation and done (#393) (c3f13d5)

## [v0.41.1] — 2026-08-09

### Bug Fixes

- allow /dev/ device writes in plan-mode write guard (#398) (adc6c61)

### Other Changes

- Lock-retry for `git stash push` is git-version-dependent (follow-up to #357 C3) (#396) (27e0eb9)

## [v0.41.0] — 2026-08-08

### Features

- re-inject session context on start/resume/compaction (#351) (#391) (8a9c0ed)

### Other Changes

- wade review crashes with Rich MarkupError when reviewer feedback contains bracket markup (#395) (2a224a6)

## [v0.40.0] — 2026-08-08

### Features

- honor a dedicated ai.review_pr_comments config for the auto-launched review session (#390) (d6b920e)

### Other Changes

- H3: Knowledge lifecycle — worktree-local, reviewed and merged through the PR (#386) (26af39f)

## [v0.39.3] — 2026-08-07

### Bug Fixes

- bound implementation-session review loop with a code-enforced 2-pass cap (#385) (3ed4f04)

### Other Changes

- E4: Repo-quality gates — pre-commit + commit-msg git hooks, and PostToolUse feedback (#381) (123727a)
- E2: Plan-phase enforcement (auto-validate + plan Stop guard) (#378) (92e6b43)
- E1: Make `done` the authoritative completion gate (+ pre-push backstop, + realign Stop) (#375) (2445c55)

## [v0.39.2] — 2026-08-05

### Chores

- stop tracking KNOWLEDGE.md/KNOWLEDGE.ratings.yml in git (c6a78b1)

### Other Changes

- H4: Drift &amp; layering — AGENTS.md, architecture.md, crossby hook comments, raw subprocess in services (#365) (3ae0ff3)

## [v0.39.1] — 2026-08-05

### Bug Fixes

- allow shell reads outside the worktree, block only writes (#373) (d9dc842)

## [v0.39.0] — 2026-08-05

### Features

- offer only tool-valid reasoning-effort levels in wade init (#371) (cb6458a)

## [v0.38.1] — 2026-08-04

### Bug Fixes

- accept permission_mode key in config validation (#369) (cdeb48e)

### Other Changes

- H2: Git &amp; remote-state hygiene — never lose work, never act on a stale read (+ retire `merge_strategy: direct`) (#362) (6e55893)
- H1: Hook-layer correctness — capability matrix + fail-closed containment (crossby 0.13) (#363) (623805f)

## [v0.38.0] — 2026-08-03

### Features

- add a documentation update pass to the implementation and pr-comments sessions (#361) (96b6b6d)

### Other Changes

- E: Context budget — slim the skills, say each rule once, load reference just-in-time (#364) (670cc29)

## [v0.37.0] — 2026-08-01

### Features

- modernize AI-tool hook integration via crossby runtime contract (#320) (76e8ca2)

## [v0.36.0] — 2026-07-25

### Features

- announce headless review timeout so orchestrators don't kill it early (#347) (1af5754)

## [v0.35.3] — 2026-07-25

### Chores

- bump crossby to v0.11.0 and adopt newer models (Opus 5) (#345) (d9ac8a5)

## [v0.35.1] — 2026-07-24

### Bug Fixes

- PR merge fails with 'could not determine current branch' when repo_root HEAD is detached (#334) (7c79e88)

## [v0.35.0] — 2026-07-24

### Features

- refresh Antigravity support (CLI + IDE) for crossby 0.10.2 (#343) (8ff0f9a)

## [v0.34.0] — 2026-07-24

### Features

- add "auto" and "accept-edits" permission modes (#338) (5b82060)

## [v0.33.0] — 2026-07-23

### Features

- replace Gemini CLI support with Antigravity CLI (bump crossby to 0.10.x) (#336) (c54af6d)

## [v0.32.0] — 2026-07-23

### Features

- supersede existing issue when planning splits it into multiple plan files (#331) (2f1fb93)

## [v0.31.3] — 2026-07-22

### Bug Fixes

- --mode headless still blocks on TTY AI-selection confirm prompt (#332) (3095ed7)

## [v0.31.2] — 2026-07-17

### Refactoring

- convert init_service.py into a package of focused modules (#325) (25d19ef)

## [v0.31.1] — 2026-07-17

### Bug Fixes

- remove(stale=True) misclassifies worktree as ACTIVE when provider read fails in CI (regression from #315) (#327) (83edda9)

## [v0.31.0] — 2026-07-17

### Features

- modularize implementation_service/core.py into focused submodules (#315) (97c5767)

## [v0.30.3] — 2026-07-17

### Bug Fixes

- skip self-upgrade for editable uv-tool installs (#323) (65666ec)

## [v0.30.2] — 2026-07-16

### Bug Fixes

- prevent infinite self-upgrade re-exec loop in `wade update` (#322) (1e7afc7)

## [v0.30.1] — 2026-07-16

### CI/CD

- use GitHub App token for auto-version push (#319) (7045ef8)

## [v0.30.0] — 2026-07-16

### Features

- add `stale` rating to knowledge entries (56f6f44)

## [v0.29.9] — 2026-07-16

### Bug Fixes

- prevent auto-version workflow race condition on concurrent PR merges (2657e8c)

## [v0.29.8] — 2026-07-16

### Bug Fixes

- accept 'in-progress' as a synonym for 'in_progress' in Markdown task state parsing (6d03639)

## [v0.29.7] — 2026-07-16

### Refactoring

- refine auto-stash sync — skip redundant fetch, extend --no-stash to review session, drop dead stash helpers (45b5f49)

## [v0.29.6] — 2026-07-16

### Build

- bump crossby to v0.3.1 (07be390)

## [v0.29.5] — 2026-07-15

### Bug Fixes

- pin typer below 0.26 to avoid CLI usage-string breakage (3b8f234)

## [v0.29.4] — 2026-07-15

### Documentation

- document release auto-bump hook and PyPI trusted publishing (d38a87e)

## [v0.29.3] — 2026-07-15

### Build

- source crossby from PyPI instead of git (15937e0)

## [v0.29.2] — 2026-07-15

### CI/CD

- restore release and publish workflows (8a5ddc3)

## [v0.29.1] — 2026-07-15

### Documentation

- reflect crossby AI tool layer migration, remove dead artifacts (6927543)

## [v0.29.0] — 2026-07-14

### Features

- replace internal AI tool layer with the crossby dependency (#215) (9e8e92c)

## [v0.28.0] — 2026-07-13

### Features

- add current Claude models to registry and fix model probe (#309) (76b87f7)

## [v0.27.0] — 2026-05-15

### Features

- add Markdown file-backed task provider (#304) (a234bda)

## [v0.26.0] — 2026-05-15

### Features

- auto-stash dirty worktree during sync/catchup with safe recovery (#306) (155ec18)

## [v0.25.0] — 2026-04-29

### Features

- skip review-poll settle wait when comments are already settled (#303) (107c3de)

## [v0.24.2] — 2026-04-28

### Refactoring

- stop applying yolo to headless AI delegation (#301) (86b3245)

## [v0.24.1] — 2026-04-27

### Bug Fixes

- ensure_label produces spurious subprocess.failed error when label exists (#299) (bb557e7)

## [v0.24.0] — 2026-04-27

### Features

- rework wade init wizard — order, skippability, effort/yolo defaults (#294) (0b098a0)

## [v0.23.0] — 2026-04-27

### Features

- require knowledge rating after retrieval and reinforce entry quality rules (#296) (01b70de)

## [v0.22.0] — 2026-04-27

### Features

- plan-session prompt should ask open-ended question, not suggest options (#292) (79cab7c)

## [v0.21.0] — 2026-04-26

### Features

- add entry-writing style guide to knowledge skill (#291) (9cced4d)

## [v0.20.6] — 2026-04-25

### Bug Fixes

- plan and implementation session prompts instruct agent to read full KNOWLEDGE.md before topic is known (#287) (11779cc)

## [v0.20.5] — 2026-04-24

### Bug Fixes

- guard hooks fail open due to Claude Code JSON schema validation rejecting extra fields (#285) (c10e5e2)

## [v0.20.4] — 2026-04-24

### Documentation

- limit review cycles to at most 2 runs in skill partials (#289) (a165a5a)

## [v0.20.3] — 2026-04-23

### Bug Fixes

- knowledge search fails for plain ## Title entries and dumps full file (#283) (240c09d)

## [v0.20.2] — 2026-04-19

### Bug Fixes

- integrate knowledge search step into plan-session workflow (#279) (ac05154)

## [v0.20.1] — 2026-04-18

### Bug Fixes

- search knowledge after learning feature topic in plan-session skill (#277) (dbfb0e1)

## [v0.20.0] — 2026-04-18

### Features

- add Claude Opus 4.7, xhigh effort level, and refresh model registry (#275) (a8916d3)

## [v0.19.8] — 2026-04-11

### Tests

- pin unit test console width to 80 to catch CI wrapping regressions (8e9b1de)

## [v0.19.7] — 2026-04-11

### Bug Fixes

- prevent success messages from wrapping in narrow CI terminals (9870e95)

## [v0.19.6] — 2026-04-11

### Bug Fixes

- knowledge commands write/read worktree-local KNOWLEDGE.md instead of main repo (#273) (c56a201)

## [v0.19.5] — 2026-04-11

### Bug Fixes

- knowledge get --search silently ignores filters when knowledge file has no entries (#271) (8993657)

## [v0.19.4] — 2026-04-10

### Refactoring

- split implementation_service.py into package + add git layer wrappers (43e43ce)

## [v0.19.3] — 2026-04-10

### Bug Fixes

- continue polling when pending reviewers exist after review bot completes (#269) (c95e990)

## [v0.19.2] — 2026-04-10

### Bug Fixes

- reorder implementation session prompt to match plan session structure (#267) (53b83f8)

## [v0.19.1] — 2026-04-09

### Refactoring

- deduplicate code, fix bugs, and improve abstractions across codebase (34e9cc1)

## [v0.19.0] — 2026-04-09

### Features

- overhaul knowledge system with tagging, search, and contextual retrieval (#261) (f9b2fec)

## [v0.18.0] — 2026-04-09

### Features

- make review steps conditional in skill files based on wade.yml config (#265) (7a4e1c3)

## [v0.17.0] — 2026-04-09

### Features

- add TODO-based workflow tracking to session skills (#263) (40f3384)

## [v0.16.0] — 2026-04-09

### Features

- add knowledge enable/disable CLI commands (#259) (c4601cb)

## [v0.15.15] — 2026-04-08

### Bug Fixes

- detect committed session-specific files on `done` and `sync` (#257) (0170ec8)

## [v0.15.14] — 2026-04-02

### Refactoring

- remove commit prompt from `wade init` (#255) (3c87092)

## [v0.15.13] — 2026-04-02

### Bug Fixes

- update test to check manifest at .wade/.wade-managed after #251 move (39712fd)

## [v0.15.12] — 2026-04-02

### Bug Fixes

- plan-session write guard fails open on hook execution errors (#253) (5f5ccb8)

## [v0.15.11] — 2026-04-02

### Refactoring

- eliminate committed .gitignore block — move all wade artifacts to worktree-only (#251) (d1e12e9)

## [v0.15.10] — 2026-04-01

### Bug Fixes

- Gemini CLI integration errors — hooks format, deprecated allowed-tools, and positional args (#249) (71fde4b)

## [v0.15.9] — 2026-03-31

### Bug Fixes

- relax knowledge entry ID regex to allow descriptive IDs (#247) (51d85ae)

## [v0.15.8] — 2026-03-31

### Bug Fixes

- PR comment polling misses outdated threads and PR-level reviews (#245) (82c03ec)

## [v0.15.7] — 2026-03-30

### Bug Fixes

- strengthen review enforcement in implementation-session done command (#239) (1f1043e)
- detect completed bot reviews in PR comment polling (#243) (c7589e0)

## [v0.15.6] — 2026-03-30

### Bug Fixes

- plan-session and worktree guard hooks not blocking writes in Claude Code (#241) (dadaf82)

## [v0.15.5] — 2026-03-28

### Chores

- audit and fix gitignore patterns for wade-managed artifacts (#237) (be2661d)

## [v0.15.4] — 2026-03-27

### Chores

- add worktree_guard.py to gitignore for all AI tool dirs (639a7d1)

## [v0.15.3] — 2026-03-28

### Bug Fixes

- batch mode bugs and orchestrator improvements (#228) (0314b75)

## [v0.15.2] — 2026-03-28

### Bug Fixes

- add inline dialog reminders at key decision points in session skills (#236) (34ca81b)

## [v0.15.1] — 2026-03-28

### Refactoring

- move AGENTS.md pointer and AI tool settings from main to worktree-only (#234) (27007f0)

## [v0.15.0] — 2026-03-27

### Features

- add catchup step to sync worktree with base branch at implementation startup (#232) (947b5ce)

## [v0.14.0] — 2026-03-27

### Features

- add worktree file-write guard hook for implementation sessions (#230) (39f2543)

## [v0.13.1] — 2026-03-27

### Bug Fixes

- add retry logic for transient gh CLI network failures (976a83f)

## [v0.13.0] — 2026-03-27

### Features

- add workflow recap and state summary to all session skills (#223) (5171e7b)

## [v0.12.0] — 2026-03-27

### Features

- instruct AI agents to use native confirmation and question components (#225) (2ce2785)

## [v0.11.3] — 2026-03-26

### Bug Fixes

- batch mode stacked branches and chain-aware review (#218) (6115d0c)
- propagate yolo_explicit flag through review session handoff (#220) (bf88950)

## [v0.11.2] — 2026-03-25

### Tests

- rehabilitate workflow test reliability (#146) (26771d3)

## [v0.11.1] — 2026-03-24

### Bug Fixes

- prevent wade-managed skills from being committed in inited projects (#214) (6f7e789)

### Other Changes

- Knowledge service thumbs-up/down (#212) (8063192)

## [v0.11.0] — 2026-03-24

### Features

- auto-copy internal wade files to worktrees regardless of user config (0b81145)
- add timeout field to AICommandConfig and wire through review delegation (073c57b)

## [v0.10.0] — 2026-03-24

### Features

- add `knowledge get` command to read project knowledge file (#211) (bc1c05d)

## [v0.9.1] — 2026-03-22

### Bug Fixes

- config validation rejects `knowledge` key in .wade.yml (#209) (2dc7d5d)

## [v0.9.0] — 2026-03-22

### Features

- add project knowledge file for cross-session AI learning (#207) (6130c49)

## [v0.8.3] — 2026-03-20

### Documentation

- add hooks_util.py to architecture package structure tree (c2c97be)

## [v0.8.2] — 2026-03-20

### Documentation

- fix documentation inconsistencies and outdated references across codebase (bde4258)

## [v0.8.1] — 2026-03-20

### Refactoring

- reduce duplication and clean up dead code across services, providers, and config (#204) (bb6ae70)

## [v0.8.0] — 2026-03-20

### Features

- poll for PR comments in smart_start review flow (#202) (4f1d49e)

## [v0.7.2] — 2026-03-19

### Bug Fixes

- gracefully handle deleted GitHub issues in worktree list (#200) (b49d81e)

## [v0.7.1] — 2026-03-19

### Bug Fixes

- configure_plan_hooks emits invalid string hooks instead of required objects (#198) (d78b2c0)

## [v0.7.0] — 2026-03-19

### Features

- make review pr-comments polling commit-aware (#196) (80d6684)

## [v0.6.2] — 2026-03-19

### Bug Fixes

- use colon format Shell(wade:*) for Cursor CLI permission pattern (a11f0b2)

## [v0.6.1] — 2026-03-19

### Bug Fixes

- validate conventional commit prefix in plan titles at plan-session done (0eceb43)

### Other Changes

- Add "Open PR in browser" option to wade smart-start menu (#194) (00ea1ba)
- Make "Wait for reviews" actively poll for review comments (#192) (437b8cf)

## [v0.6.0] — 2026-03-19

### Features

- require conventional commit prefix in plan issue titles (0f6e773)

## [v0.5.10] — 2026-03-19

### Bug Fixes

- use colon format Bash(wade:*) for Claude Code permission pattern (3b7bee8)

## [v0.5.9] — 2026-03-19

### CI/CD

- add PR title linter to enforce conventional commit format (4d75edb)

## [v0.5.8] — 2026-03-19

### Bug Fixes

- read PR title instead of push commit message for version bump detection (ada6a26)

### Other Changes

- Add file-write guard hooks to plan session worktrees (#177) (fd01fdc)
- Effort configuration overhaul: Claude --effort flag + WADE_EFFORT env var (#190) (350c1bd)
- Fix review_batch default delegation mode from prompt to interactive (#188) (609ab36)

## [v0.5.7] — 2026-03-18

### Bug Fixes

- use full checklist-fallback pattern in implementation_service.start() (810e56f)

## [v0.5.6] — 2026-03-18

### Bug Fixes

- skip re-merge of already-MERGED branches in batch review integration branch (30be5cb)

### Other Changes

- Implement chain auto-continuation with merge-gated dependencies (#181) (9e30d6d)

## [v0.5.5] — 2026-03-18

### Bug Fixes

- reuse existing PR on batch review re-run (54dbf6e)

## [v0.5.4] — 2026-03-18

### Bug Fixes

- force-push integration branch on batch review re-run (61e4559)

### Other Changes

- Detect tracking issues in wade implement and wade <N> (#183) (31903be)
- Fix Ghostty terminal launch and add batch terminal launcher (#179) (3214463)

## [v0.5.3] — 2026-03-18

### Bug Fixes

- always propagate wade allowlist to worktrees unconditionally (6c9ebab)

### Other Changes

- Update model registries and default tier mappings for new OpenAI/Google models (#168) (64664c9)

## [v0.5.2] — 2026-03-18

### Documentation

- replace `python` with `uv run python` in version bump commands (8a3b52a)

## [v0.5.1] — 2026-03-18

### Other Changes

- Add CLI argument probing to probe script and improve Codex model discovery (#172) (5f29c56)
- Fall back to branch diff when `wade review implementation` finds no working-tree changes (#175) (72d5d79)
- Update Codex and Gemini adapters for new CLI capabilities (#170) (7ea9173)
- Comprehensive PR review status reporting (#162) (58d94c9)
- Fix `[NO PLAN]` badge showing for planned issues (#166) (a37e9f2)
- Rename "Self-review" to "Review" in implementation-session skill template (#164) (d67e70f)
- Make self-review output actionable for AI agents (#160) (9a3a45a)
- Fix init review/deps prompt ordering and self-review UX (#156) (a80e855)
- Fix plan-phase AI config leaking into implementation handoff (#158) (415ca7a)
- Enable Codex headless (non-interactive) execution via `exec` subcommand (#154) (c772d48)
- Fix medium complexity tier defaulting to fast-tier model in init (#152) (46ad404)
- Remove project scripts auto-detection from allowed commands (#148) (1289296)
- Fix prompt mode to return raw prompt without user-facing header (#150) (cd1d858)

## [v0.5.0] — 2026-03-12

### Features

- show review reminder in done commands (#145) (5b0f0ca)

### Other Changes

- Add post-batch coherence review session (#144) (96a5abc)
- Audit and fix skills, prompts, and session commands (#143) (cd18999)
- Show plan status badge in issue listing (#142) (a074615)

## [v0.4.2] — 2026-03-11

### Bug Fixes

- fetch_reviews() uses fragile branch-name reconstruction — prefer current branch (#140) (29598e9)

### Other Changes

- Align internal naming with CLI commands (#138) (0d057c6)
- Investigate adding AI-facing directives to work session boundary commands (#136) (65ca021)

## [v0.4.1] — 2026-03-10

### Bug Fixes

- check only latest CodeRabbit comment for review bot status (fd5626e)

## [v0.4.0] — 2026-03-10

### Features

- detect CodeRabbit review bot status and clarify review prompt wording (0b34717)

### Other Changes

- Add YOLO mode: skip AI tool permission prompts during work sessions (#135) (c9728aa)

## [v0.3.3] — 2026-03-10

### Bug Fixes

- hide smart-start from help, show `wade <N>` shorthand instead (aaa44a3)

## [v0.3.2] — 2026-03-10

### Bug Fixes

- add fallback instruction for PLAN.md in implement prompt (c416780)

## [v0.3.1] — 2026-03-10

### Bug Fixes

- lazy-load ClickUp provider to avoid import failure when httpx is missing (3911dd2)

## [v0.3.0] — 2026-03-10

### Features

- add multi-provider support with ClickUp (#134) (6188856)

## [v0.2.2] — 2026-03-10

### Bug Fixes

- fix delegation config parsing, prompt-mode deps, and rename prompt templates (02e7deb)

## [v0.2.1] — 2026-03-09

### Bug Fixes

- correct Claude Code session path encoding and add preservation to PR merge (6b698bf)

## [v0.2.0] — 2026-03-09

### Features

- unify delegation infrastructure across deps and review commands (#123, #124, #125) (805cabe)

### Other Changes

- Add generic AI delegation infrastructure with plan review and code review as first consumers (#122) (8cdcf43)
- Pass AI params through _offer_to_implement after planning (#129) (e4291d0)
- Defer AI selection until after plan check in work_service.start() (#127) (63ea499)
- Allow changing AI tool and model in auto-deps confirmation prompt (#132) (98017e1)

## [v0.1.6] — 2026-03-08

### Bug Fixes

- scope plan file discovery to PLAN*.md in planning worktrees (e51516a)

## [v0.1.5] — 2026-03-08

### Bug Fixes

- check all issue states when detecting duplicate tracking issues (#119) (d4f6743)

## [v0.1.4] — 2026-03-08

### Bug Fixes

- prevent shell injection in auto-version workflow (#117) (513e69a)

### Other Changes

- Selective per-command skill installation (#114) (ad1b2ea)
- Add "Resume session" option to "Continue working" menu (#112) (a208013)
- Prompt for CLI completion install during init (#116) (8a4e117)
- Reorganize CLI commands around workflow phases and audience separation (#110) (5aa1d00)
- Offer to start working session after planning (#107) (4bd677d)

## [v0.1.3] — 2026-03-06

### Bug Fixes

- prevent duplicate tracking issues in dependency analysis (8b660ae)

### Other Changes

- Make smart-start menu context-aware based on PR draft state and worktree (#102) (b7020cf)
- Fix terminal tab title for address-reviews sessions (#99) (926dd70)

## [v0.1.2] — 2026-03-06

### CI/CD

- add auto version bump workflow on PR merge (3a57876)

### Other Changes

- Add model thinking/effort mode support (#81) (7c57dc3)

## [v0.1.1] — 2026-03-06

### Bug Fixes

- always append usage entry to PR/issue body after session capture (6fc15af)

### Other Changes

- Add `wade plan-done` command for deterministic plan validation (#73) (ab760b1)
- Preserve AI tool session data on worktree deletion (#75) (e3cc093)
- Unify and complete AI tool permission pre-authorization (#76) (802e97a)
- Add `wade address-reviews` command to address PR review comments (#79) (ad00ee2)
- Support Cursor CLI (#72) (b13d2b5)
- Set terminal tab title during plan sessions (#70) (3a44666)
- Make wade new-task work non-interactively (#71) (fed841b)

## [v0.1.0] — 2026-03-05

### Features

- prompt for setup-worktree script during `wade init` (3742141)

## [v0.0.6] — 2026-03-05

### Bug Fixes

- remove snapshot-based issue detection from plan-task (bdce128)

### Other Changes

- Fix: include issue description in work-session initial prompt (#67) (da94b0a)
- Fix `wade init` not updating config values on re-init (#65) (e01ed4e)
- Add option to open PR and/or issue (#63) (1dbbc1d)
- Add interactive AI tool/model confirmation before launching AI sessions (#61) (f3979e6)

## [v0.0.5] — 2026-03-05

### Chores

- update statusline template — suppress branch in worktrees, replace bar with percentage (576faf1)

### Other Changes

- Make bootstrap_worktree self-init aware for wade development (#57) (3bbea63)
- Fix worktree dirty state caused by untracked wade init files (#47) (06c267a)
- Build setup-worktree.sh script (#53) (bfb11a9)
- Document and wire `setup-worktree` hook (#52) (aac3057)
- Audit and update all documentation for current command names and workflow (#44) (2907d57)
- Improve multiline input UX: fix Ctrl+D hint and tip shown to user (#43) (8b9272a)

## [v0.0.4] — 2026-03-04

### Chores

- remove unused Docker and script files (b110bc0)

## [v0.0.3] — 2026-03-04

### Bug Fixes

- update install.sh to use wade-cli package name (bb65a72)

### Chores

- add manual release script (40c4270)
- remove release and publish workflows (cd9b42d)
