---
name: code-review
description: Review a code change for correctness, security, maintainability, tests, and scope alignment.
---

# Code review methodology

Read the complete diff in context and follow changed values through callers,
models, persistence, and external boundaries. Prioritize concrete correctness
bugs, security failures, data loss, broken compatibility, concurrency or state
errors, and missing regression tests where an untested behavior can plausibly
fail. Anchor each concern to the intended behavior, public contracts, and
established repository invariants; verify error paths and edge conditions as
carefully as the happy path.

Reference exact files and lines, explain the observable failure, and suggest the
smallest robust fix. Omit style preferences, alternative abstractions, generic
maintainability advice, unrelated legacy cleanup, and hypothetical risks without
a plausible execution path. If no material defect exists, say so briefly.
