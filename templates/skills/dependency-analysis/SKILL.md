---
name: dependency-analysis
description: Infer the minimal acyclic prerequisite graph among a set of software tasks.
---

# Dependency-analysis methodology

For each task, identify the artifacts, APIs, schemas, migrations, or behavior it
requires and produces. Add a prerequisite only when one task cannot be safely
implemented or validated before another. Prefer shared-foundation dependencies
over incidental file overlap, and do not confuse a convenient order with a true
requirement.

Remove redundant transitive edges, check the graph for cycles, and reconsider
any cycle as evidence that tasks should be reshaped or share an earlier
foundation. Explain each direct edge with the concrete prerequisite it carries.
