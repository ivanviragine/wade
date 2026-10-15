---
name: plan-review
description: Review a software implementation plan for completeness, feasibility, correctness, risk, and ordering.
---

# Plan review methodology

Check that the plan can achieve its stated goal and acceptance criteria: verify
goal coverage, feasibility, architecture compatibility, ordering, failure
behavior, testability, and implementation surfaces that materially affect the
outcome. Use repository evidence to evaluate assumptions, contracts, and
dependencies.

Treat explicit non-goals and accepted design choices as boundaries. Recommend
the smallest plan correction for a demonstrated defect; do not replace the
design unless evidence shows it is incorrect or unsafe. State the concrete
failure mode, deficient plan section, and correction for each finding. If the
plan is sound, say so directly.
