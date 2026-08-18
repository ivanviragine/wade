Before a **consequential or outward-facing** operation — pushing, creating a PR
or issue, closing the session (e.g. `done`) — give a one-line heads-up of what
you're about to do and why, and never run it silently. That is consent, not
narration.

### Communication style — report by exception

Default to terse. On success, report only the actionable handles — issue/PR
numbers, URLs, the next command. **Nudge** on real caveats — non-default
behavior, a gate that will block, an irreversible or outward-facing effect,
anything that deviates from what the user asked. **Omit** noise — step recaps,
"nothing broke" reassurance, restating obvious next steps. A required outcome
(e.g. the doc-update report) is not noise: surface it, but in one terse line.

When you surface **anything that needs the developer** — an action item, a
caveat, or a decision — always state the **recommended action**; never present a
problem or choice without saying what you'd do. For a genuine fix/no-fix
decision, also give **what it is**, brief context, and its **complexity**
(`easy`/`medium`/`complex`/`very_complex`), then ask via the native question
component with the recommended option first, labelled "(recommended)" — e.g.
"Cursor adapter passes the wrong model. Easy fix. Fix now (recommended) or open
an issue?" A plain caveat needs the recommendation, not a complexity tag.

**Signal exit only after the gate.** Do not call the session complete, done, or
finished, or say it is safe to exit, until its authoritative closing gate
(`done`) has actually succeeded. Before the gate, describe progress in progress
terms ("code written, tests pass — next: review, then `done`"), never with
exit/complete wording. After it succeeds, say so plainly and imperatively in one
sentence, pairing "complete" only with "exit" — e.g. "Session complete — PR #N
updated; exit now." Never emit a bare "complete"/"done" a user could read as
permission to leave.

**End every session** with the emoji step-status summary — one `✅`/`⚠️`/`❌`/`⏭️` per step, the handles, an explicit attention line, a bold `Next:` action — not a prose recap. Make **every** finite-choice decision a native dialog (first option `(recommended)`; each label names the next step it triggers) — never a prose question; open-ended prompts (e.g. "What would you like to plan?") stay plain text. Legend + examples: @.claude/skills/task/reference/session-summary-format.md. One sentence each, context *before* the question. Key decision points:
