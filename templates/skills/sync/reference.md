# ghaiw work sync — Reference

## Synopsis

```
ghaiw work sync [--main-branch NAME] [--dry-run] [--json] [--help]
```

## Options

| Flag               | Description                                      | Default       |
|--------------------|--------------------------------------------------|---------------|
| `--main-branch`    | Override the main branch name                    | Auto-detect (`main` or `master`) |
| `--dry-run`        | Print what would happen without making changes   | Off           |
| `--json`           | Emit structured JSON events on stdout            | Off (human-readable events) |
| `--help`, `-h`     | Show usage and exit                              | —             |

## Exit Codes

| Code | Meaning                           | Action                                      |
|------|-----------------------------------|---------------------------------------------|
| `0`  | Success — branch up to date       | Report success, ready for merge to main     |
| `1`  | General / unexpected error        | Report error details                        |
| `2`  | Conflict during merge main → feature | Resolve conflicts, then re-run            |
| `4`  | Pre-flight check failed           | Fix the issue (dirty tree, wrong branch...) |

## Output Channels

- **stderr** — Human-readable colored log lines (`[sync] ...`)
- **stdout** — Structured events (JSON when `--json`, plain otherwise)

Always parse stdout for programmatic consumption. Stderr is for display only.

## JSON Event Schema

All events are single-line JSON objects with an `"event"` key.

### `preflight_ok`

Emitted after all pre-flight checks pass.

```json
{"event":"preflight_ok","current_branch":"feat/42-my-feature","main_branch":"main"}
```

### `up_to_date`

Branch is already current with main. Nothing to merge.

```json
{"event":"up_to_date","branch":"feat/42-my-feature","main":"main"}
```

### `merged`

Main was successfully merged into the feature branch.

```json
{"event":"merged","commits_merged":"3"}
```

### `conflict`

Merge started but conflicts were found. The merge is paused (not aborted).

```json
{"event":"conflict","source":"main","target":"feat/42-my-feature","files":"src/app.py\nsrc/config.py"}
```

Fields:
- `files` — newline-separated list of conflicted file paths.

### `conflict_diff`

Immediately follows `conflict`. Contains the full diff of conflicted files.

```json
{"event":"conflict_diff","diff":"<escaped diff content>"}
```

The `diff` value has escaped newlines (`\n`), tabs (`\t`), and quotes.

### `dry_run`

Emitted when `--dry-run` is active and a merge would have been performed.

```json
{"event":"dry_run","action":"merge_main_into_feature","behind":"5"}
```

### `error`

Pre-flight or runtime failure. Check `reason` for the cause.

```json
{"event":"error","reason":"dirty_worktree","staged":"yes","unstaged":"no","untracked":"yes"}
```

Known `reason` values:

| Reason             | Meaning                                       |
|--------------------|-----------------------------------------------|
| `not_git_repo`     | Not inside a git repository                   |
| `detached_head`    | HEAD is detached (no branch)                  |
| `no_main_branch`   | Could not auto-detect main/master             |
| `on_main_branch`   | Already on the main branch                    |
| `dirty_worktree`   | Uncommitted changes (includes `staged`, `unstaged`, `untracked` fields) |

### `done`

Final event on success. Signals the branch is ready to merge into main.

```json
{"event":"done","branch":"feat/42-my-feature","main":"main"}
```

## Pre-flight Checks (in order)

1. Inside a git repository
2. On a named branch (not detached HEAD)
3. Main branch exists (auto-detect or `--main-branch`)
4. Not on the main branch itself
5. Working tree is clean (no staged, unstaged, or untracked changes)

## Merge Strategy

1. **Fetch** — `git fetch <remote> <main>` (skips if no remote configured)
2. **Merge** — `git merge <main> --no-edit` into the current feature branch
3. On conflict: exits with code 2, merge is paused (not aborted)
4. After resolution: caller runs `git add -A && git commit --no-edit`, then re-runs the command
