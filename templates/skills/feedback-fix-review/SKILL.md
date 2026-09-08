---
name: feedback-fix-review
description: Review feedback-driven corrections for verified defects and direct regressions.
---

# Feedback-fix review methodology

Inspect the feedback-driven edits and their directly affected behavior. Verify
that each correction resolves the intended verified defect while preserving
surrounding contracts, error handling, and relevant regression coverage.

Report only concrete regressions or incomplete corrections with evidence, an
observable failure, and the smallest necessary fix. Do not re-review untouched
implementation or reopen accepted design unless the correction itself exposes a
material correctness or safety failure. If the correction is sound, say so
briefly.
