---
name: code-review
description: Review a code change for correctness, security, maintainability, tests, and scope alignment.
---

# Code review methodology

Read the complete diff in context and follow changed values through callers,
models, persistence, and external boundaries. Prioritize concrete correctness
bugs, security failures, data loss, broken compatibility, race or state errors,
and missing tests. Verify error paths and edge conditions as carefully as the
happy path.

If a bounded review exits non-timeout status 1, treat it as an execution error:
diagnose the failure before retrying, and never treat it as a successful review.

Check whether the implementation satisfies its stated goal without unrelated
scope, duplicate abstractions, or stale legacy behavior. Reference exact files
and lines, explain the observable failure, and suggest the smallest robust fix.
Distinguish required fixes from optional improvements. If no actionable issue
exists, say so briefly.
