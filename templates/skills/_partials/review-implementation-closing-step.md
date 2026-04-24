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

**Run at most 2 times total**:
- If findings are **minor** (style, small fixes): Address them and proceed to
  Step 2; no re-review needed.
- If findings are **major** (logic errors, architectural issues): Address them
  and re-run once. Always proceed to Step 2 after the 2nd run, regardless of
  new findings.
- Never re-run more than twice.

**This step is mandatory when `review_implementation.enabled` is not `false`.
Do NOT proceed to Step 2 until this step is complete and any actionable
findings are addressed and committed.**
