# Plan output contract

Compose one authoritative Markdown artifact, not a transcript.

In an interactive terminal session, pass the exact native file to
`wade plan-session done <plan_dir> --from-file <native-plan-file>`. If no native
file exists, supply the complete artifact through `--from-stdin` using a quoted
heredoc. WADE materializes the named members and reports missing reviews; review
those files while the native CLI is open, address findings, then run done again.
Resubmitting an updated artifact replaces only members owned by this session.
A changed plan requires a new review. Knowledge observations stay in the envelope.
Do not leave Plan mode or approve implementation to save a plan.

Without an interactive handoff file, return the artifact; WADE imports it after
Crossby closes the collected session.

For one task without a knowledge handoff, return its Markdown directly. WADE
names it `PLAN.md`. Each task needs exactly one H1 conventional-commit title,
`## Complexity` (`easy`, `medium`, `complex`, or `very_complex`), context, tasks,
acceptance criteria, and verification. Use `## Base Branch` only to declare one
non-default base. Plans describe implementation; they do not perform it.

For multiple tasks, relationships, or knowledge observations, return exactly this
versioned envelope: the marker followed by one JSON fence, with no outside prose.
Only a marker at the start selects bundle parsing; examples inside a normal
single-plan document remain plan content.

<!-- wade:plan-bundle:v2 -->
```json
{
  "plans": [
    {
      "filename": "PLAN-foundation.md",
      "markdown": "# feat: add the foundation\n\n## Complexity\nmedium\n\n## Tasks\n- [ ] Implement and test the foundation\n\n## Acceptance Criteria\n- [ ] Tests pass\n",
      "depends_on": []
    },
    {
      "filename": "PLAN-client.md",
      "markdown": "# feat: add the client\n\n## Complexity\nmedium\n\n## Tasks\n- [ ] Implement and test the client\n\n## Acceptance Criteria\n- [ ] Tests pass\n",
      "depends_on": ["PLAN-foundation.md"]
    }
  ],
  "knowledge_proposals": []
}
```

Filenames must be `PLAN.md` or `PLAN-<alphanumeric-slug>.md` (hyphens and
underscores allowed); no paths or case-insensitive duplicates. Dependencies name
members of this envelope, with no cycles. Titles and complexity live only in each
member's Markdown, so there are no conflicting duplicate fields. Arbitrary
headings or separators never imply another task.

When knowledge is enabled, include `knowledge_proposals` explicitly. Each item is
`{"entry_id":"existing-id","body_digest":"sha256:<64 hex>","feedback":"useful",
"note":"Observed usefulness","plan_filename":"PLAN-foundation.md"}`. Feedback
is `useful`, `not-useful`, or `needs-recheck`. Derive the body digest from the
parsed claim via `wade knowledge reconcile --json`; do not hash heading metadata.
An empty list means no observations. A member filename selects the actual task
created from that member; unspecified ownership in a multi-plan bundle stays
unresolved. These are observations, never assessments. The parent persists them
in the ignored common-directory queue after review, then binds verified task
creation before cleanup. Do not write tracked knowledge from native Plan mode.
In `PLAN_DIR_ONLY`, return no proposals.

Legacy v1 envelopes/`knowledge_votes` remain readable only as historical-feedback
proposals; do not supply both forms. New materialized workflows use v2. Frozen
older workflows are not silently reinterpreted: use an explicit supported skill
refresh/recovery and revalidate its current handoff/review binding.

Malformed envelopes, unsafe paths, duplicate names, and invalid relationships
are rejected as a whole. Content-validation failures may be salvaged as a
user-confirmed, dependency-complete valid subset. Never silently collapse a
multi-task artifact into one issue or approve implementation to finish collection.
