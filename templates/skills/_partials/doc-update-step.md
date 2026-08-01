Re-read what this session changed (`git diff <base-branch>...HEAD`) and ask what
it invalidates. Update {doc_targets} — plus anything else the change makes wrong.

Do not skip because the change is small: a new flag, a renamed option, or a
changed default all need docs. Commit doc changes with the work, not as a
follow-up.

**State the outcome before moving on** — either the files you updated, or "no
doc changes needed: not user-facing, no change to architecture, commands, or
conventions." Proceeding silently is not a valid result for this step.
