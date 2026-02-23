---
name: pr-summary
description: >
  Write a comprehensive PR summary during your work session to provide context
  for human reviewers. Captures what was done, why, key changes, and includes
  screenshots. Strongly recommended — ghaiw work done warns if missing.
---

# PR Summary

Write a comprehensive summary of your work session that will be used as the PR
description when you run `ghaiwpy work done`.

**Strongly recommended:** `ghaiwpy work done` will show a warning if this file is
missing (checked at `/tmp/PR-SUMMARY-{issue-number}.md`), and the PR will
have a minimal description instead of your summary.

## When to activate

This skill activates during **implementation work** in a worktree session. Write
the summary as you work, updating it throughout the session.

**When to write:**
- After making significant changes
- **Before running `ghaiwpy work done`** (mandatory)
- When you've completed testing and verification
- After taking screenshots of UI changes

## Where to write

Write the summary to **`/tmp/PR-SUMMARY-{issue-number}.md`** (e.g. `/tmp/PR-SUMMARY-42.md`
for issue #42). Using the issue number in the filename keeps parallel sessions
isolated — each worktree works on a different issue so filenames never collide.

`ghaiwpy work done` picks up the file automatically by looking for
`/tmp/PR-SUMMARY-{issue-number}.md`.

## What to include

Your PR summary should provide a **TLDR of the agent session** for human
reviewers. Think of it as a conversation summary that helps reviewers understand:

1. **What was accomplished** — high-level summary of the changes
2. **Why these changes** — context from the issue/plan
3. **Key technical decisions** — important implementation choices
4. **What was tested** — how you verified the changes work
5. **Screenshots** — visual evidence for UI/UX changes

## Format

Use this template as a starting point:

```markdown
# PR Summary

## What was done

[High-level summary in 2-3 sentences describing what you accomplished]

## Context

[Why these changes were needed - reference the issue/plan]

## Changes

[Bullet list of key changes:]
- Added X feature to improve Y
- Modified Z to handle edge case W
- Refactored A for better B

## Technical decisions

[Important implementation choices, if any:]
- Chose approach X over Y because...
- Used library Z for...

## Testing

[How you verified the changes:]
- Tested scenario A: result
- Tested scenario B: result
- Ran test suite: all passing

## Screenshots

[If you have UI changes, include screenshots:]

### Feature X in action
![description](screenshots/feature-x.png)

### Before and after
![before](screenshots/before.png)
![after](screenshots/after.png)

## Notes for reviewers

[Optional: anything the reviewer should know:]
- Pay attention to file X where Y happens
- Known limitation: Z (will address in follow-up)
```

## Tips for writing effective summaries

**Be concise but complete:**
- Aim for 200-500 words total
- Focus on "what" and "why", not exhaustive "how"
- Use bullet points for readability

**Include visuals:**
- Screenshots are powerful — include them for any UI changes
- Save screenshots to `screenshots/` directory
- Reference them with relative paths from repository root (e.g., `screenshots/feature-x.png`)

**Think like a reviewer:**
- What would you want to know if reviewing this code?
- What questions might they have?
- What context is missing from just reading the diff?

**Update as you work:**
- Don't wait until the end to write the summary
- Update it as you make progress
- It's easier to write in small increments

## What happens next

When you run `ghaiwpy work done`:

1. The command looks for `/tmp/PR-SUMMARY-{issue-number}.md`
2. If found, it uses your summary as the PR description
3. If the linked issue has a managed `## Plan Summary` block (from `task plan`), ghaiw includes that usage section in the PR body automatically (including input/output/cached token counts and model breakdowns when present)
4. If not found, it shows a warning and creates a minimal PR body
5. Screenshots referenced in your summary are included (must be committed)

Your `/tmp/PR-SUMMARY-{issue-number}.md` is **not committed** — it lives outside the repo and is
only used to generate the PR description. Think of it as a scratch pad for
documenting your work.

## Example session workflow

```bash
# Human starts the work session from terminal (not from inside an AI session)
ghaiw work start 42

# AI agent makes changes...
# AI agent creates /tmp/PR-SUMMARY-42.md and writes initial summary

# AI agent continues working, updates /tmp/PR-SUMMARY-42.md as it goes...
# AI agent takes screenshots, saves to screenshots/ directory

# Before finishing
cat /tmp/PR-SUMMARY-42.md  # Review your summary

# Finalize
ghaiw work sync
ghaiw work done     # Uses /tmp/PR-SUMMARY-42.md to create PR description
```

## Integration with workflow

This skill is referenced in Rule 5 of the workflow skill. Before running
`ghaiwpy work done`, write your PR summary to ensure reviewers have adequate
context.

**Strongly recommended:** Write `/tmp/PR-SUMMARY-{issue-number}.md` for all work sessions so reviewers
have adequate context. If you made UI changes, include screenshots.
