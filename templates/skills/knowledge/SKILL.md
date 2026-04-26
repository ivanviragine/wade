---
name: knowledge
description: >
  Instructions for contextual knowledge retrieval, tagging, search, and rating.
  Referenced by plan-session and implementation-session skills.
---

# Knowledge Operations

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

Rate entries you read — this feeds the auto-filter that prunes low-quality
entries over time:

```bash
wade knowledge rate <entry-id> up    # entry was useful
wade knowledge rate <entry-id> down  # entry was outdated or misleading
```

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

Capture important patterns, conventions, or gotchas discovered during a session:

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
