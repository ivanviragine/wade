# Session summary & decision format

The shared convention for **how every wade session (plan / implementation /
review) and the `task` / `deps` skills end**, and **how they ask the user to
decide**. Loaded on demand — the inline "Present results" step in each skill is
self-sufficient without this file; this adds the legend and worked examples.

## Emoji step-status legend

- ✅ step completed successfully
- ⚠️ completed **but needs the user's attention** — always paired with a one-line reason
- ❌ step **failed or is blocked** — with the reason and the recommended action

## Final summary skeleton (session end)

End the session with a compact block — no prose recap:

1. **One status line for all steps**, each led by its glyph and joined with ` · `
   (e.g. `✅ Review · ✅ Docs · ✅ Sync · ✅ Done`).
2. **Actionable handles** on their own line — issue/PR number + URL, `closes #N
   on merge` (or `stays open` when `--no-close`), branch when relevant.
3. **An attention line** — either `✅ Nothing needs your attention.` or
   `⚠️ N item(s) need your attention:` followed by terse bullets, one per item,
   each with its reason.
4. **A bold `Next:` line** naming the single next action.

## Decisions — always a native dialog

Every decision with a **finite set of choices** uses your tool's native
dialog/question component, never a prose question. The first option is marked
**(recommended)**, and every option **label names the resulting next step** so
the choice itself communicates what happens. The end-of-session exit decision:

- `Exit now — wade takes over (recommended)`
- `Keep editing — I have more changes`

**Open-ended input is NOT a dialog.** A prompt with no enumerable answer (e.g.
"What would you like to plan?") stays a plain-text question — explicitly do
**not** force a native selection/question component for it.

## Worked examples

### Implementation session (clean run)

```
✅ Review · ✅ Docs (README, AGENTS.md) · ✅ PR-SUMMARY · ✅ Sync · ✅ Done
PR #123 — https://github.com/…/pull/123 (closes #120 on merge)
✅ Nothing needs your attention.
Next: exit — wade cleans up the worktree after merge.
```

Then the exit decision as a native dialog: `Exit now — wade takes over
(recommended)` / `Keep editing — I have more changes`.

### Review session (with a caveat)

```
✅ Docs · ✅ PR-SUMMARY · ✅ Sync · ✅ Done
PR #123 — https://github.com/…/pull/123 · 4/5 threads resolved
⚠️ 1 item needs your attention:
   • Thread on auth.py left unresolved — reviewer asked for a design decision only you can make.
Next: exit — reviewers are notified of your changes.
```

Exit dialog: `Exit now — reviewers are notified (recommended)` / `Keep editing —
I have more changes`.

### Plan session (clean run)

```
✅ Plan file(s) written · ✅ Review · ✅ Knowledge rated · ✅ Validated
2 plan file(s): feat-x (complex), feat-y (medium)
✅ Nothing needs your attention.
Next: exit — wade creates the issue(s) and draft PR(s), then run `wade implement <issue>`.
```

Plan-session decisions that **are** dialogs: "write the plan file(s) now?",
"revise or continue?", and the final exit decision. The single plain-text
exception is step 1, "What would you like to plan?" — never a dialog.

## Cross-tool note (known limitation)

This convention is verified against **Claude Code's** rendering. Other
crossby-adapted tools (Cursor, Copilot, Codex, OpenCode, Antigravity, VS Code)
may degrade the emoji glyphs or the dialog to plain text. That is a known
limitation, not a regression: the inline spec reads fine as plain text, so the
summary stays usable everywhere.
