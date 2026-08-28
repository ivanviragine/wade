## Review budget and skip policy

Trust the exact budget and pass count printed by the review command. Do not
kill, background, or early-exit a bounded review before that budget elapses.

A headless timeout is a budget overrun, not a successful review. Use any
salvaged findings, retry once, and do not loop on the same commit. A non-timeout
exit 1 is an execution error to diagnose. Implementation review pass caps are
enforced by `done.max_review_passes`; plan review has no code-tracked cap and
PR-comment review is uncapped.

Implementation and PR-comment review may be skipped only for objectively
trivial docs/comments, formatting, metadata, generated-file, or tiny no-logic
changes. Never skip logic, security/auth, public API, dependency, or migration
changes. Name the applied criterion in the commit or `PR-SUMMARY.md`; the
visible PR review status lets a human ratify the judgment.
