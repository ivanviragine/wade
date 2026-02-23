# sync — Examples

## Clean merge (no conflicts)

```console
$ ghaiw work sync --json
{"event":"preflight_ok","current_branch":"feat/42-add-auth","main_branch":"main"}
{"event":"merged","commits_merged":"3"}
{"event":"done","branch":"feat/42-add-auth","main":"main"}
```

stderr (human-readable):
```
[sync] Preparing branch for merge...
[sync] Current branch: feat/42-add-auth
[sync] Main branch:    main
[sync] Step 1/2: Fetching latest from remote...
[sync] Fetched latest main from origin.
[sync] Step 2/2: Merging main → feat/42-add-auth...
[sync] Branch is 3 commit(s) behind main.
[sync] Merged main into feat/42-add-auth successfully.

[sync] Branch 'feat/42-add-auth' is ready to merge into 'main'.
```

## Already up to date

```console
$ ghaiw work sync --json
{"event":"preflight_ok","current_branch":"feat/7-fix-typo","main_branch":"main"}
{"event":"up_to_date","branch":"feat/7-fix-typo","main":"main"}
{"event":"done","branch":"feat/7-fix-typo","main":"main"}
```

## Merge conflict

```console
$ ghaiw work sync --json
{"event":"preflight_ok","current_branch":"feat/15-refactor","main_branch":"main"}
{"event":"conflict","source":"main","target":"feat/15-refactor","files":"src/app.py"}
{"event":"conflict_diff","diff":"diff --cc src/app.py\n..."}
```

Exit code: `2`. The merge is paused. To resolve:

```bash
# AI reads conflicted files with conflict markers
# AI suggests resolution, user confirms
# Apply resolution, then:
git add -A && git commit --no-edit

# Re-run to verify:
ghaiw work sync --json
# Should now emit: {"event":"up_to_date",...} + {"event":"done",...}
```

## Dirty worktree (needs commit first)

```console
$ ghaiw work sync --json
{"event":"error","reason":"dirty_worktree","staged":"no","unstaged":"yes","untracked":"yes"}
```

Fix: commit all changes, then re-run.

## Running on main (rejected)

```console
$ ghaiw work sync --json
{"event":"error","reason":"on_main_branch"}
```

Exit code: `4`. Switch to a feature branch first.

## Dry run

```console
$ ghaiw work sync --dry-run --json
{"event":"preflight_ok","current_branch":"feat/42-add-auth","main_branch":"main"}
{"event":"dry_run","action":"merge_main_into_feature","behind":"5"}
```

No changes are made. Shows what would happen.

## Full workflow (commit → merge → done)

```bash
# Step 1: Commit if needed
git status --porcelain
# Output: M  src/auth.py

git add -A
git commit -m "feat: add new auth schema"

# Step 2: Run sync
ghaiw work sync --json
# {"event":"preflight_ok",...}
# {"event":"merged","commits_merged":"1"}
# {"event":"done","branch":"feat/42-add-auth","main":"main"}
```
