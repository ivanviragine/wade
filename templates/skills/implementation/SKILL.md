---
name: implementation
description: Implement a scoped software change safely from repository evidence through verification.
---

# Implementation methodology

Translate the requested behavior into observable acceptance cases before
editing. Read the owning abstractions, neighboring code, tests, and public
contracts. Preserve established dependency direction and naming unless the
change deliberately replaces them.

Work in small coherent increments. Put deterministic decisions in code, keep
data models explicit, reject invalid states early, and make failure behavior as
deliberate as success behavior. Preserve compatibility only where required and
make transitional paths visible rather than silently ambiguous.

Add focused tests with each behavior change, including boundary and regression
cases. Run the narrowest useful checks while iterating, then the complete
project-prescribed verification. Review the final diff for scope, accidental
duplication, stale paths, security implications, and user-facing changes.
