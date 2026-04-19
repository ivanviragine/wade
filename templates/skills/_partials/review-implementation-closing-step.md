**Step 1 — Review [MANDATORY]:**

Run `wade review implementation` to review your changes and check the exit code:
- **Exit 0**: Review completed externally or skipped. If there is output, it is
  review feedback — read it and address any actionable findings, then commit
  before proceeding.
- **Exit 2**: Self-review mode. The output is a review prompt — you must act as
  the reviewer: read the instructions, analyze the diff, identify issues, and
  fix them. Commit fixes before proceeding.
- **Exit 1**: Error — debug and retry.

For staged-only review: `wade review implementation --staged`.

**This step is mandatory when `review_implementation.enabled` is not `false`.
Do NOT proceed to Step 2 until this step is complete and any actionable
findings are addressed and committed.**
