## What was done
After `wade init` (without committing), `wade work start` created worktrees where
wade-managed files appeared as untracked in `git status`, blocking `work done` and
`work sync` clean checks. This fix extends `.gitignore` to cover all wade-managed
paths and adds a commit-or-local prompt to `wade init` so worktrees stay clean.

## Changes
- Extended `GITIGNORE_ENTRIES` to include `.wade.yml`, `.claude/skills/`,
  `.github/skills`, `.agents/`, `.gemini/` — previously only `.wade/`,
  `.wade-managed`, `PLAN.md`, and `PR-SUMMARY.md` were listed
- Added `_prompt_commit_or_local()` to `init()` — after writing all files,
  asks whether to commit wade files or keep them local
- Commit path: `_commit_wade_files()` runs `git add --force` + `git commit`
  to commit all managed files despite gitignore
- No-commit path: `_configure_local_worktree()` adds `.wade.yml` to
  `copy_to_worktree` in the config and hints user to commit `.gitignore`
- Non-interactive mode defaults to the no-commit path

## Testing
- 16 new unit tests covering `_configure_local_worktree`, `_commit_wade_files`,
  `_prompt_commit_or_local`, and `GITIGNORE_ENTRIES` coverage
- Full integration test verifying non-interactive init sets `copy_to_worktree`
- Full suite: 894 passed, 1 pre-existing failure (unrelated), 7 skipped

## Notes for reviewers
- The original plan used per-worktree `$GIT_DIR/info/exclude` but this was
  proven not to work (git only reads `info/exclude` from `$GIT_COMMON_DIR`)
- `.gitignore` entries are harmless for already-committed files — they only
  affect untracked files, so extending the entries is always safe
- `git add --force` overrides gitignore for the commit path
