---
name: knowledge
description: >
  Retrieve project knowledge, inspect committed evidence, and preserve owner-bound observations.
---

# Knowledge operations

Search relevant entries with `wade knowledge get --search "worktree safety"`
or `--tag git`. Search and tag combine with OR. `--raw` prints original Markdown;
`--all-history` includes each exact-duplicate member and its own receipts.
Default visibility never hides a claim because of legacy negative/stale votes.
`--min-score N` and `--no-filter` remain explicit usefulness filters.

Treat these dimensions separately:

- Assessment: unverified, checked-current, contradicted, or needs-recheck.
- Publication: branch-local, integrated, or unavailable, independently for claim
  and each assessment. Integrated means matching ID/content in locally available
  configured main; an intermediate PR base is not publication.
- Usefulness: useful/not-useful and historical up/down/stale counts.
- Historical relationships: superseded/retired and exact-duplicate membership.

Dates, utility votes, aliases, and publication never establish correctness.
Plain entries remain unverified until the owning implementation explicitly runs
`wade knowledge identify <one-based-position>`.

## Check committed proof in the owning worktree

```
wade knowledge check <id> current --file src/example.py#L1-L20 --note "Observed invariant"
wade knowledge check <id> contradicted --file src/example.py --note "Observed contradiction"
wade knowledge check <id> current --evidence-file proof.json --note "Captured decision"
```

Repeat `--file` for multiple ordinary tracked blobs. Dirty/staged/untracked,
missing, symlink and submodule sources are rejected. WADE derives HEAD, blob ID,
body digest and timestamp. A receipt committed after its source remains valid
when the compared blob is unchanged, including rebase/squash and unavailable
historical commits. Changed/renamed/missing paths require rechecking. Resolve
incompatible live assessments explicitly with repeatable `--replaces <event-id>`;
line order and newer timestamps do not decide authority.

`--evidence-file` contains a JSON array of tagged evidence:

- `git_file`: path, checked_commit, blob, optional start_line/end_line. Supplied
  file evidence must match freshly derived HEAD proof.
- `test_run`: check, command, checked_commit, outcome (passed/failed), optional
  result_digest. WADE records supplied results; it does not execute the command.
  Another commit makes test-only proof historical/needs-recheck. Unchanged mixed
  code proof can remain applicable while the old test is explicitly historical.
- `external_url`: url, timezone-aware fetched_at, content_digest. This is a supplied
  fetched snapshot, never a claim that reconciliation fetched current remote bytes.
- `decision`: reference to a PR, issue or ADR. It remains applicable until the
  entry is explicitly superseded/retired; an ADR may also have file evidence.

Digests use `sha256:<64 lowercase hex>`; commits/blob IDs use full Git object IDs.

## Utility and historical feedback

`wade knowledge feedback <id> useful|not-useful --note <reason>` records usefulness.
Deprecated `wade knowledge rate <id> up|down|stale` remains historical feedback only.
An observation in planning, dependency analysis, main, a detached checkout, or a
bounded reviewer goes to an ignored owner-bound proposal queue. It never writes
tracked knowledge in main or another branch.

## Proposals and recovery

```
wade knowledge proposals list
wade knowledge proposals retain <proposal-id> --note "Awaiting original task"
wade knowledge proposals discard <proposal-id> --note "No longer relevant"
wade knowledge proposals accept <proposal-id>
wade knowledge check <id> current --proposal <proposal-id> --file src/example.py --note "Fresh check"
wade knowledge proposals recover --worktree <original-retained-parent>
wade knowledge proposals recover --worktree <original-retained-parent> --from-main
```

Acceptance requires the original completed parent's persisted task/member
association, matching repository/provider/task and unchanged claim content.
It grants no correctness: assess fresh committed evidence separately. Multi-plan
observations without a specific member remain pending. Bounded plan/dependency
workers retain their original parent or service-verified task association before
launch; ambiguous multi-task destinations remain pending after cleanup.
Queue originals and
auditable dispositions survive worktree cleanup. Recovery preserves original
legacy bytes, never drains/reset main's old spool, and never guesses ownership
from the next worktree or a caller-supplied task number.

## Add, tag and reconcile

Capture only non-obvious durable facts, at most three sentences per claim; omit
work logs, changelogs and facts already documented. In the owning implementation:

```
echo "Durable fact" | wade knowledge add --session implementation --kind invariant --claim-key domain-rule --tag git
wade knowledge tag add <id> <tag>
wade knowledge tag remove <id> <tag>
wade knowledge add --session implementation --supersedes <old-id>
wade knowledge reconcile --against HEAD --check --json
wade knowledge reconcile --against HEAD --apply
```

Tag names use lowercase kebab-case, at most30 characters. Semantic kind defaults
to neutral `note`; a session label is not a semantic kind. Exact duplicates require
identical normalized claim text, kind and optional key; only line endings and
outer whitespace normalize. The smallest ID represents the group for display.
Apply appends deterministic digest-bound links, preserves all prose/events, and
never transfers a member's assessment. Repeated/concurrent applies converge.
Check is the default and leaves HEAD, index and working files unchanged, never
fetching. Unverified/needs-recheck/duplicate entries are valid; structural event
conflicts and invalid references are errors. Commit knowledge before final review;
closing gates reconcile after sync and again after any recovery merge.
