# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Conventional Commits](https://conventionalcommits.org/).

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
