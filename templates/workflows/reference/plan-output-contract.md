# Plan output contract

Return one authoritative native Markdown artifact, not filesystem instructions
or a transcript. WADE imports it after Crossby closes the native session.

For one task without a knowledge handoff, return its Markdown directly. WADE
names it `PLAN.md`. Each task needs exactly one H1 conventional-commit title,
`## Complexity` (`easy`, `medium`, `complex`, or `very_complex`), context, tasks,
acceptance criteria, and verification. Use `## Base Branch` only to declare one
non-default base. Plans describe implementation; they do not perform it.

For multiple tasks, relationships, or knowledge ratings, return exactly this
versioned envelope: the marker followed by one JSON fence, with no outside prose.

<!-- wade:plan-bundle:v1 -->
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
  "knowledge_votes": []
}
```

Filenames must be `PLAN.md` or `PLAN-<alphanumeric-slug>.md` (hyphens and
underscores allowed); no paths or case-insensitive duplicates. Dependencies name
members of this envelope, with no cycles. Titles and complexity live only in each
member's Markdown, so there are no conflicting duplicate fields. Arbitrary
headings or separators never imply another task.

When knowledge is enabled, include `knowledge_votes` explicitly. Each item is
`{"entry_id": "existing-entry-id", "direction": "up"}` with direction `up`,
`down`, or `stale`, at most once per evaluated entry. An empty list means no
evaluated entry warrants a vote. The parent validates IDs and stages votes; do
not write them from native Plan mode. In `PLAN_DIR_ONLY`, return no votes.

Malformed envelopes, unsafe paths, duplicate names, and invalid relationships
are rejected as a whole. Content-validation failures may be salvaged as a
user-confirmed, dependency-complete valid subset. Never silently collapse a
multi-task artifact into one issue or approve implementation to finish collection.
