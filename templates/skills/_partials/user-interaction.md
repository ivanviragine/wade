Before a **consequential or outward-facing** operation — pushing, creating a PR
or issue, closing the session (e.g. `done`) — give a one-line heads-up of what
you're about to do and why, and never run it silently. That is consent, not
narration.

### Communication style — report by exception

Default to terse. On success, report only the actionable handles — issue/PR
numbers, URLs, the next command — never a list of completed steps or a
reassurance that nothing broke.

When something needs the developer's attention, or a decision only they can
make, state it in 1–2 sentences — **what it is**, brief context, its
**complexity** (`easy`/`medium`/`complex`/`very_complex`), and a
**recommendation** — then ask via the native question component with the
recommended option first, labelled "(recommended)". E.g. "Cursor adapter passes
the wrong model. Easy fix. Fix now (recommended) or open an issue?"

Use your tool's native confirmation/question components at decision points — not questions embedded in prose. Keep each to one sentence, with context *before* the question. Key decision points:
