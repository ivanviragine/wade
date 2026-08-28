---
name: batch-review
description: Review multiple related change sets for integration coherence and safe merge ordering.
---

# Batch review methodology

Evaluate each change on its own and in the combined system. Compare naming,
data shapes, APIs, shared state, migrations, and cross-cutting behavior. Detect
overlapping edits, duplicated solutions, incompatible assumptions, missing
connections, and tests that pass separately but not together.

For stacked changes, distinguish incremental behavior from what the parent
already provides. Recommend a merge order based on real dependencies and
conflict reduction. Tie every finding to the affected change sets and describe
the integration failure it would cause. If the set is coherent, say so.
