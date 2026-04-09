# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Conventional Commits](https://conventionalcommits.org/).

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
