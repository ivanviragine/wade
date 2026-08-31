---
name: plan-review
description: Review a software implementation plan for completeness, feasibility, correctness, risk, and ordering.
---

# Plan review methodology

Check that the goal and non-goals are unambiguous and that the proposed design
matches the current architecture. Trace every affected entry point, model,
persistence boundary, migration, compatibility path, error case, test surface,
and documentation surface. Look for hidden coupling, duplicated sources of
truth, unsafe ordering, unverifiable acceptance criteria, and assumptions not
supported by repository evidence.

Classify findings by impact and make each one actionable: explain the concrete
failure mode, where the plan is deficient, and the smallest correction. Do not
inflate preferences into blockers. If the plan is sound, say so directly.
