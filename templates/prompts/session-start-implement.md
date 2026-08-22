Implementation phase; the full plan lives in PLAN.md at the worktree root. First action: run `wade implementation-session check`; proceed only when it reports `IN_WORKTREE`.
Wrap up with `wade implementation-session done`: it syncs onto base, pushes, and opens or updates the PR — skip the manual `git push` / `gh pr create`.
Expect gates first: review findings to resolve, a base-branch sync/rebase, and a pre-push backstop that refuses an unmarked push.
Review budget: `wade review implementation` prints your live time budget and pass count — trust those over any number in the skill text.
