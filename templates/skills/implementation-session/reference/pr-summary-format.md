# PR Summary Reference

`wade implementation-session done` reads `PR-SUMMARY.md` from the worktree root
to populate the PR body. If the file is missing, the PR has no description.

## What to include

1. **What was accomplished** — high-level summary of changes
2. **Why these changes** — context from the issue/plan
3. **Key technical decisions** — important implementation choices
4. **What was tested** — how you verified the changes work

## Format

```markdown
## What was done
[High-level summary in 2-3 sentences]

## Changes
- Added X to improve Y
- Modified Z to handle edge case W

## Testing
- Tested scenario A: result
- Ran test suite: all passing

## Notes for reviewers
[Optional: anything the reviewer should know]
```

## Never commit this file

`PR-SUMMARY.md` is a session artifact (already gitignored). If `git status` shows
it as tracked/modified, untrack it first:

```bash
git rm --cached PR-SUMMARY.md
git commit -m "chore: untrack PR-SUMMARY.md (already gitignored)"
```

Then re-write the file — it will be ignored going forward.
