# Lifecycle recovery

Load this only when readiness, catchup, or sync reports a conflict or capability
failure. Follow the command's exact remediation and re-run the same lifecycle
command. Do not substitute raw GitHub/provider mutations for WADE orchestration.

## Readiness failures

Grant only the worktree Git metadata path, GitHub credential/API route, or local
knowledge staging path named by `reason=...`. Never disable the sandbox or grant
main-checkout write access broadly. If the capability cannot be granted narrowly,
stop before editing and request a trusted relaunch.

## Catchup

Implementation startup normally auto-stashes tracked local changes, merges the
configured base, and restores them. Session artifacts are excluded. A genuine
content conflict is aborted and leaves the worktree clean; catchup may report the
conflicting paths but does not leave a merge to resolve. A `.wade/stale_base`
reminder means do not begin work until a later catchup/sync reaches up to date.

If catchup reports an untracked collision, commit, move, or remove only the named
project-owned path, then retry. WADE-owned regenerable collisions are reconciled
by startup code; do not invent a blanket cleanup command.

## Sync result

`wade implementation-session sync --json` and
`wade review-pr-comments-session sync --json` use the same result shape:

- exit 0: synchronized; continue the workflow;
- exit 2: conflict; with auto-stash, the merge was aborted and the stash was
  restored, so resolve the base conflict deliberately and rerun sync; with a
  deliberately clean/no-stash merge left in progress, resolve every unmerged
  file, stage only those resolutions, complete the merge, then rerun sync;
- exit 4: preflight/capability failure; correct the named condition and retry.

For an intentionally manual conflict resolution, fetch the configured base and
merge that exact remote base, then:

1. list unmerged files with `git diff --name-only --diff-filter=U`;
2. understand both sides and remove conflict markers;
3. stage only the resolved files;
4. complete the merge with `git commit --no-edit`;
5. rerun the session sync command.

If the result reports `stash_left_behind`, the merge succeeded but stash restore
conflicted. Apply the exact stash ref printed by the command, resolve its files in
place, and do not create a second merge commit.

## Hidden pointer collision

If Git says `AGENTS.md` would be overwritten while status appears clean, first
inspect `git ls-files -v AGENTS.md`. A leading `S` is WADE's skip-worktree pointer
handling. Clear that bit and inspect the diff. Stop if it contains anything
beyond the marker-delimited generated pointer: real project instruction edits
must be preserved. Only a pointer-only difference may be discarded before
retrying sync; bootstrap restores the current pointer and skip-worktree state.
Never restore `AGENTS.md` from a pre-merge copy because that would discard base
changes while skip-worktree hides the loss.
