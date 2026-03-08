# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Conventional Commits](https://conventionalcommits.org/).

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
