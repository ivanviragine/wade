# Documentation Policies

Detailed documentation rules, feedback policies, and the full change checklist. For the compact checklist, see `AGENTS.md`.

## Documentation Rules

Every change **must** include documentation updates as part of the implementation — not as a follow-up. Before finalizing any work:

1. **`AGENTS.md`** — Update if the change affects architecture, commands, conventions, design principles, or development workflow.
2. **`README.md`** — Update if the change affects user-facing behavior: new commands, flags, install steps, configuration options, or supported tools.
3. **`templates/skills/plan-session/SKILL.md`** / **`templates/skills/implementation-session/SKILL.md`** / **`templates/skills/review-pr-comments-session/SKILL.md`** — *(inited-project artifacts)* Update if the change affects phase-specific session rules (planning rules in plan-session, implementation rules in implementation-session, review rules in review-pr-comments-session).
4. **`templates/skills/`** (task, deps) — *(inited-project artifacts)* Update if the change affects how AI agents in inited projects should use wade commands. This is where command references, flags, workflows, and examples belong.
5. **`templates/agents-pointer.md`** — *(inited-project artifact)* The pointer text that `wade init` injects into target projects' `AGENTS.md`. Update this when the critical inline rules or pointer wording changes. **This is not the same as this repo's own `## Git Workflow` section** — that is the self-installed copy, written once by `wade init` and never overwritten by `wade update`.
6. **`docs/dev/`** — Update the relevant supplementary doc if the change affects architecture details, testing patterns, extension guides, or skills system internals.

Do not skip documentation even for "small" changes — a new flag, a renamed option, or a changed default all need docs updates. Documentation is part of "done", not a separate task.

## Full Change Checklist

Before considering any work complete, verify each item:

- [ ] **Code** — `./scripts/test.sh` passes
- [ ] **Types + Lint** — `./scripts/check.sh` passes (or run both at once: `./scripts/check-all.sh`)
- [ ] **`AGENTS.md`** — updated if architecture, conventions, design principles, or workflow changed
- [ ] **`README.md`** — updated if user-facing behavior changed (commands, flags, config, install)
- [ ] **`templates/skills/plan-session/SKILL.md`** / **`implementation-session/SKILL.md`** / **`review-pr-comments-session/SKILL.md`** — updated if phase-specific session rules changed
- [ ] **`templates/agents-pointer.md`** — updated if the critical inline rules or pointer wording changed
- [ ] **`templates/skills/`** (task, deps) — updated if agent-facing command workflows, flags, or examples changed
- [ ] **Phase skills** *(inited-project artifacts)* — planning rules go in plan-session, implementation rules go in implementation-session, review rules go in review-pr-comments-session, AGENTS.md pointer stays minimal
- [ ] **`docs/dev/`** — updated if architecture details, testing patterns, extension guides, or skills system docs changed
- [ ] **Commit** — uses conventional-commit prefix for correct auto-versioning

## Documentation Feedback Loop

After completing any task, reflect on whether you encountered any friction during the session — ambiguity, missing context, misleading guidance, unexpected behavior, or anything that required more investigation than it should have. **Friction is a signal.** Act on it before closing the session.

**At the end of every session, ask yourself:**

1. **Did I hit a bug or unexpected behavior?**
   - Describe it to the user and **offer** to create a GitHub Issue (`wade task create`)
   - Do not silently work around it — surface it so it can be tracked and fixed

2. **Did I hit a doc gap or architecture misunderstanding?**
   - Was something in `AGENTS.md` or `docs/dev/` missing, wrong, or misleading?
   - Did I have to dig into source files to answer a question that should have been documented?
   - **Offer** to update the relevant doc to prevent future agents from hitting the same gap

3. **Did I have to investigate more than expected?**
   - Did I look at files, run commands, or make assumptions to fill in missing context?
   - If so, that context belongs in the docs — **offer** to add it

**The goal: every friction point should happen at most once.** If a future agent would hit the same issue, the documentation has failed.

Always **offer** — don't create issues or edit docs without the user's confirmation. But never skip the reflection.

## Correction-Driven Documentation

When the user corrects you — changes your approach, points out a wrong assumption, or redirects your reasoning — that correction reveals a gap in the documentation. **Do not just fix the immediate task.** Also, **in the same response where you acknowledge the correction**:

1. **Identify the root cause** — What documentation was missing, ambiguous, or misleading that led to the wrong approach? If a rule already existed but you rationalized around it, the rule's wording is the gap. Ask: was the existing wording precise enough to prevent the rationalization? If not, that is a documentation failure, not just an adherence failure.
2. **Propose a documentation update** — Suggest adding or tightening the rule in the appropriate file (`AGENTS.md`, a `docs/dev/` file, a skill, or `templates/agents-pointer.md`). Do this in the same response as the acknowledgement — not as a follow-up, not after the user asks.
3. **Apply the update** — If the user agrees, add the rule immediately as part of the current task. Documentation updates from corrections are not follow-ups.

The goal: every correction should happen **at most once**. If a future agent would make the same mistake, the documentation has failed.
