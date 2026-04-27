---
name: knowledge
description: >
  Instructions for contextual knowledge retrieval, tagging, search, and rating.
  Referenced by plan-session, implementation-session, and review-pr-comments-session skills.
---

# Knowledge Operations

## Purpose

Knowledge captures **gotchas, buried constraints, hidden invariants, and facts not in `AGENTS.md`/`CLAUDE.md`** — things a future agent would otherwise waste time rediscovering. It is not a work log, a changelog, or a duplicate of documentation that already exists. If the fact is in `AGENTS.md`, `CLAUDE.md`, or derivable from `git log`/`git blame`, do not add it.

## Contextual retrieval

Do **not** dump all knowledge at session start. Instead, search for entries
relevant to your current task:

```bash
wade knowledge get --search "worktree safety"
wade knowledge get --tag git
wade knowledge get --search "error handling" --tag testing
```

When both `--search` and `--tag` are provided, entries matching **either**
criterion are returned (OR semantics).

Only fall back to `wade knowledge get` (no filters) if you need a broad overview
or the knowledge base is small.

## Search syntax

The `--search` flag accepts boolean queries:

| Syntax | Meaning |
|--------|---------|
| `term` | Entries containing the term (case-insensitive) |
| `"exact phrase"` | Entries containing the exact phrase |
| `term1 AND term2` | Both terms must appear |
| `term1 OR term2` | Either term may appear |
| `NOT term` | Exclude entries containing the term |
| `(a OR b) AND c` | Parentheses for grouping |
| `worktree safety` | Bare space = implicit AND |

## Tagging

### When to tag

Tag entries when they relate to specific topics that future sessions might
search for. Good tags describe the **domain** (e.g. `git`, `testing`,
`ci`, `error-handling`), not the action (avoid `fix`, `add`, `update`).

### Tag format

- Lowercase kebab-case: `git`, `worktree-safety`, `error-handling`
- Alphanumeric + hyphens only, no spaces
- Max 30 characters per tag

### Adding tags

When creating an entry:
```bash
echo "Learning" | wade knowledge add --session plan --tag git --tag worktree
```

Managing tags on existing entries:
```bash
wade knowledge tag add <entry-id> <tag>
wade knowledge tag remove <entry-id> <tag>
wade knowledge tag list              # all unique tags
wade knowledge tag list <entry-id>   # tags for one entry
```

## Rating

**For each entry you open and evaluate, make an explicit decision per the decision tree below** — not per search call. A broad search returning many results does not force you to rate all of them; only entries you actually read and assessed require a decision. Rate up/down when appropriate; otherwise intentionally leave the entry unrated.

```bash
wade knowledge rate <entry-id> up    # entry was useful
wade knowledge rate <entry-id> down  # entry was outdated or misleading
```

**How to decide:**

1. **Was your search appropriate (correct tags/text)?**
   - **No** → do not rate. The issue is your search, not the entry.

2. Search was appropriate; entry came back legitimately:
   - **Useful for your task** → rate **up**.
   - **Correct content but not applicable to your task** → do **not** rate (entry is fine; refine future searches).
   - **Content is outdated or incorrect** → rate **down**.
   - **Tags or terminology are wrong/missing (content itself is correct)** → do **not** rate down — fix tags via `wade knowledge tag add/remove <id> <tag>`. Downvoting good content for tag problems mis-signals the auto-pruner.

3. **Entry surfaced due to a known parser/search bug** → do not rate; flag as a system issue.

## Entry style

Write entries that are easy to scan and reuse across sessions:

- **One insight per entry** — no multi-topic dumps; split if needed
- **Lead with the key fact** — state the finding first, context second
- **≤3 sentences** — if longer, split into separate entries
- **No preamble or hedging** — omit "I noticed that…", "It turned out that…"
- **Active voice, present tense** — "Use X when Y", not "It was found that X should be used"

**Before (verbose, hedged):**
> I noticed during testing that it turned out the sync command needs to be run after all commits are staged, otherwise it may fail with a dirty-tree error in some cases.

**After (concise, direct):**
> Run `wade implementation-session sync` only after all changes are committed — a dirty worktree causes exit code 4.

## Adding entries

Knowledge is **not a changelog** — it is a searchable store of facts, gotchas, and limitations that help future agents avoid scanning the codebase. Only capture what is non-obvious or hard to derive from the code or git history:

- Non-obvious constraints, gotchas, and footguns
- Known limitations and their workarounds
- Patterns with a WHY that code alone can't explain
- Pointers to where a rule or config lives when the location isn't obvious

**DO NOT capture:**

- Completed-work summaries: "We added X feature in this session", "Implemented Y in PR #N" — this duplicates `git log`.
- Refactor or fix histories: "Fixed bug Z by doing W" — derivable from `git blame` / `git log`.
- Content already in `AGENTS.md` or `CLAUDE.md`: anything that is documented there does not need a knowledge entry.
- Anything reconstructable from `git log`, `git blame`, or reading the current source — if a future agent can find it in 10 seconds, it doesn't belong here.

Entry dates (shown in search results) indicate freshness — rate an entry down if it no longer applies.

```bash
echo "Your learnings here" | wade knowledge add --session <type> --issue <number> --tag <topic>
```

If a new entry corrects or replaces an existing one:
```bash
echo "Corrected info" | wade knowledge add --session <type> --supersedes <old-entry-id>
```

## Filtering

By default, entries with enough negative votes are automatically pruned
(statistical auto-filter). Override with:

- `--min-score N` — hard cutoff on net score (bypasses auto-filter)
- `--no-filter` — show everything, no score filtering
