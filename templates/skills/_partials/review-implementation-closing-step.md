**Step 1 — Review [required unless the change is objectively trivial]:**

Run `wade review implementation` to review your changes and check the exit code:
- **Exit 0**: Review completed externally or skipped. If there is output, it is
  review feedback — read it and address any actionable findings, then commit
  before proceeding.
- **Exit 2**: Self-review mode. The output is a review prompt — you must act as
  the reviewer: read the instructions, analyze the diff, identify issues, and
  fix them. Commit fixes before proceeding.
- **Exit 1**: If the output contains the timeout marker (see **Review budget &
  skip guidance** above), it is a budget overrun, not a bug — re-run once, or
  use the salvaged partial output. Any other exit 1: a real error — debug and
  retry.

For staged-only review: `wade review implementation --staged`. See **Review
budget & skip guidance** above for the time budget, the pass cap, and the
may-skip / never-skip criteria for a sanctioned `--skip-review`.

After fixing a finding, commit and re-review (the commit stales the `reviewed`
marker); for major findings, re-run once — always proceed to Step 2 after that,
regardless of new findings.

Do NOT proceed to Step 2 until this step is complete (reviewed, cap-reached, or
a sanctioned skip naming its criterion) and any actionable findings are
addressed and committed.
