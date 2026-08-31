---
name: planning
description: Analyze a software change and turn it into evidence-based, cohesive implementation tasks.
---

# Planning methodology

Start from the requested outcome, then inspect the existing system before
proposing a design. Trace current call paths, data ownership, persistence,
configuration, failure handling, compatibility surfaces, and tests. Separate
facts from assumptions and challenge any premise that contradicts repository
evidence.

Define a small set of explicit invariants. Prefer changes that establish one
source of truth and preserve dependency direction. Split work only at cohesive,
independently understandable boundaries; keep migrations and compatibility with
the change they support. Identify ordering constraints, irreversible decisions,
risks, observability needs, and validation for both success and failure paths.

Each task should state the outcome, affected surfaces, important edge cases,
tests, and documentation consequences. Avoid vague tasks such as “update the
backend” and avoid prescribing code details unsupported by inspection.
