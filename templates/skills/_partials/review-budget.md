## Review budget & skip guidance

**Time budget** — a headless review prints its exact budget at launch ("wade
budgets Ns … worst-case total Ns", or "bounded by … Ns (no retry)" for an
explicit `ai.<command>.timeout`); trust that over any number here. Never kill,
background, or early-exit before it elapses — a premature kill is an infra
timeout, not a review result.

**Pass cap** — `wade review implementation` prints "review pass N of M — K
left" each run; trust that over any number here.

- **Implementation**: capped by `done.max_review_passes` (default 2),
  **code-enforced** — once spent, `done` completes anyway with a notice.
- **Plan**: no code-tracked cap — treat any pass-count guidance as advisory.
- **PR-comments**: intentionally **uncapped**.

**Timeout ≠ error** — exit 1 whose output contains `Headless review timed out
before finishing` or `Headless review timed out before producing any
output.` is a budget overrun (expected on a large diff), not a bug; only a
non-timeout exit 1 warrants "debug and retry." But a timeout writes no
review-ran marker, so it does **not** clear `done`'s gate — salvaged output is
for addressing findings, not for completing. Re-run once; if the **same
commit** keeps timing out (a repeat can't become a pass), finish via
`--skip-review` (cite the timeout) or `done.require_review: false`. Don't loop.

**Trivial-change skip** — review is required unless the change is objectively
trivial. Applies to **implementation** and **pr-comments** review (a run is
costly); a trivially small plan just needs a lighter self-review, not a skip
flag.

- *May skip*: docs/comments-only, formatting/whitespace-only,
  config/metadata/version/changelog-only, generated-file updates, tiny diffs
  with no logic change.
- *Never skip*: logic/control-flow changes, security/auth-sensitive code,
  public-API/signature changes, dependency changes, migrations.
- Skip via `--skip-review` on the session's `done` command — recorded in the
  PR's visible `## Review Status` line, so a human reviewer sees and ratifies
  every skip (it defers to review, never ships unreviewed). **Name which
  may-skip criterion applied** in your commit message or `PR-SUMMARY.md`; an
  unstated criterion is non-compliant.
- **Trade-off, stated plainly:** this is AI judgment, not a code-computed
  threshold — a deliberate departure from "if an agent can get it wrong by
  reasoning, put it in code," because diff size alone can't tell a trivial
  rename from a trivial-looking bug fix. Compensating for the missing
  enforcement: the named-criterion audit trail above, plus the visible
  `## Review Status` line that surfaces every skip to a human reviewer.
