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

**Headless review can be slow.** When `review_implementation.mode` is `headless`,
it launches an external AI subprocess that may run for a few minutes. wade prints
the budget when it starts ("can take up to Ns"). Keep it in the foreground and
allow more than that before timing out. Do not kill it early or background it — a
premature kill is an infra timeout, not a review result. Budget:
`ai.review_implementation.timeout` (300s).

**Run at most 2 times — code-enforced.** Minor findings: fix and proceed. Major
findings: fix, re-run once, then proceed regardless of new findings. `done`
counts distinct reviewed commits and, after `done.max_review_passes` (default 2)
review→fix cycles, completes anyway (with a notice) instead of looping. Stuck in
that loop? Break it with `wade implementation-session done --skip-review`.

**This step is mandatory when `review_implementation.enabled` is not `false`.
Do NOT proceed to Step 2 until this step is complete and any actionable
findings are addressed and committed.**
