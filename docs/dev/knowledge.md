# Evidence-bound project knowledge

Knowledge prose and `KNOWLEDGE.ratings.jsonl` are tracked in the owning worktree.
The sidecar now also contains immutable, schema1 typed receipts. Legacy YAML and
JSONL votes remain historical usefulness only; reads do not migrate them. First
owned append performs the existing deterministic YAML seed migration. Neither
negative usefulness nor legacy stale counts hide claims by default.

## Authority and tree identity

Assessment (`unverified`, `checked-current`, `contradicted`, `needs-recheck`),
publication (`branch-local`, `integrated`, `unavailable`), usefulness and historical
relationships are independent. `knowledge check` derives claim-body SHA256,
HEAD, ordinary tracked file blobs and UTC timestamp. Dirty/staged/untracked,
missing, symlink and submodule evidence is refused before appending. Evidence
paths marked skip-worktree or assume-unchanged fail closed: their hidden working
bytes cannot establish committed proof. Normal injected AGENTS/gitignore flags
do not block unrelated unflagged source paths, and no generated files are edited.
Local object reads disable Git replacement objects as well as lazy fetching.
Explicit
JSON evidence additionally supports recorded tests, fetched URL snapshots and
PR/issue/ADR decisions. No command automatically runs tests or fetches URL bytes.

Reconciliation resolves target, configured main and effective PR base once and
reads committed Git trees. It disables lazy object fetching and optional index
refresh. Code applicability compares target path/blob with stored blob, not
commit ancestry: source commit A followed by receipt commit B remains valid,
as do rebase/squash and unavailable historical commits. A changed, deleted or
renamed path needs recheck. Test-only proof on another commit needs recheck;
unchanged mixed code proof may remain applicable with explicitly historical
old tests. Captured URL snapshots never assert current remote bytes. Decisions
remain applicable until explicitly superseded/retired.

Publication compares same ID/content against locally available configured main,
separately for entry and each receipt. An intermediate dependency base determines
branch impact only; it never grants publication. Missing main produces unavailable,
without a fetch or guessed integration. Uncommitted knowledge is reported apart
from committed state. If displayed prose differs from the committed claim body,
`knowledge get` withholds its publication and reports prior assessments only as
historical evidence for different content. Check leaves HEAD, index and all working bytes unchanged.

Typed event IDs deduplicate identical records and reject different payloads.
Folds are independent of line order; timestamps do not establish precedence.
Incompatible concurrent live assessments need an explicit replacement event.
Missing/wrong-entry replacement IDs and replacement cycles are structural errors.
Unverified claims, stale evidence and exact duplicates are valid report states;
malformed JSON, unknown schemas, duplicate prose IDs and conflicting events fail
completion. The full report includes branch-touched claims/events and claims
whose evidence references paths changed relative to the effective PR base.

## Exact duplicates

Body digest excludes date, tags and utility metadata. Duplicate digest v1 adds
semantic kind and optional claim key to the body, normalizing only line endings
and outer whitespace. Legacy session labels are not kinds; neutral `note` is the
default. No paraphrase/semantic merging occurs. Default display uses the smallest
entry ID, but member receipts remain independent and visible with `--all-history`
or reconciliation JSON. `--raw` returns all original Markdown. `--apply` appends
deterministic digest-bound aliases, retains prose/history and converges under
repetition/concurrent append. Changed text, kind, key or a new smaller member
causes fresh groups/links; an old alias never transfers correctness or suppresses
newly different prose. Plain entries remain readable/unverified; `identify`
explicitly assigns an ID in the owning implementation before assessment.

## Ownership, proposals and recovery

Tracked mutation requires an attached linked implementation or PR-comment
worktree with its current valid frozen task/session bundle and an immutable
`.wade/knowledge-author.json` association to the original worktree path, branch,
repository, provider and task. Switching branches cannot transfer authority or
reassign that association through skill refresh. Legacy sessions missing it must
explicitly refresh their frozen skills before authoring; they cannot synthesize
an association through an ordinary read. Initial composition uses the effective
configuration supplied to bootstrap even before main's configuration is copied
into an older dependency checkout. Subsequent actor reads validate actual local
configuration; normal preflight/full-bootstrap and plan-to-implementation
transitions retain the same original author association. Main, detached
planning/dependency analysis and all bounded delegations observe only, including
when they share an author's checkout. Delegation sets a scoped child environment
and distinct invocation identity, restores the parent environment on every exit,
and cannot grant itself author authority via a methodology binding digest.

Observations persist directly in ignored Git-common-dir `.wade/knowledge/`
partitions, not main's tracked sidecar. Each schema1 proposal retains repository
identity, operation/session identity, provider, originating task when known,
base commit, entry/body digest, original payload, proposal ID and optional plan
member. Parent task bindings identify the actual persisted destination task. Bounded
worker proposals use the registered base even if an author advances live HEAD;
a changed claim snapshot requires a fresh operation rather than corrupting the
repository queue.
A completed planning parent records that association after task persistence and
before offering child implementation or cleanup. Multi-plan observations without
member ownership remain unresolved; another worktree cannot claim them by
supplying a task number. Accept verifies repository/provider/task, original
parent binding and unchanged claim. It writes an auditable neutral acceptance,
never an assessment; a subsequent explicit check creates fresh committed proof.
Acceptance payloads are canonicalized once under the shared queue lock, so valid
same-task worktrees reuse the same timestamp and event bytes on repeated accept.
Retain/discard/accepted/assessed dispositions keep original proposal bytes.
Bounded plan reviewers retain their original parent session and reviewed member;
in-plan dependency observers retain that parent, and are associated only when
its actual persisted destination is unambiguous. Standalone dependency analysis
records service-verified task identities and its frozen invocation before launch.
A single verified destination remains usable after cleanup; multiple or failed
lookups remain pending. All workers remain observation-only.

No bootstrap, review startup, completion or automatic retained-worktree sweep
accepts feedback, drains main's old spool, resets tracked files or guesses owner.
Legacy transport logs remain pending. `knowledge proposals recover --worktree`
verifies the original retained parent's identity, archives full original bytes
and payloads durably into the ignored queue, and preserves the source log. Add `--from-main` to recover only uncommitted
main records bearing that original parent's explicit handoff ID; main bytes
remain unchanged and foreign/unresolved records stay pending.
Missing owner proof or corrupt/unsafe paths retain originals and report failure.
Both native and validated collected parents support legacy recovery from actual
persistence progress; neither relies on a synthetic completion flag. New
observations survive disposable-worktree cleanup without a spool.

Plan artifact protocol v2 adds typed `knowledge_proposals`; v1 historical votes
remain readable. Session workflow revisions advance (plan4, implementation3,
PR-comment3) so old bundles require explicit refresh/recovery validation rather
than silent reinterpretation. For an intact retained native or collected parent,
run `wade plan-session refresh <original .wade/plans>`: old frozen bytes are durably
archived and original artifacts/identity/provider progress are preserved. Native
refresh closes old review/completion gates before replacement; run a genuine fresh
review for every plan, `plan-session done`, then `plan --recover`. Interrupted native
refresh keeps a durable pending flag and requires the same explicit retry.
Collected refresh creates no native completion state: `plan --recover` performs
its existing parent review before persistence. An incomplete composition fails
frozen-bundle validation; retry explicit refresh against the retained original.
Refresh itself never dispatches a reviewer or provider mutation. Receipt/proposal event schemas begin at1; no
session-manifest schema change is needed because proposal ownership freezes the
actual operation/session and task association independently of methodology.

A tolerant `knowledge get` read reports structural evidence problems and
withholds correctness labels for invalid history, including conflicting payloads
under one durable event ID. Raw prose remains readable; corrupted history never
silently promotes the first payload to checked-current.

## Boundaries and closing gates

`models/knowledge.py` is pure typed data. `git/knowledge.py` owns local Git queries.
`utils/knowledge_events.py` and `utils/knowledge_file.py` are pure folds/parsers
that may import leaf models, never services. Ownership, append persistence,
proposals, assessment and reconciliation services orchestrate; CLI only parses
and renders. Generated session bundles/support projections are never edited.

Knowledge changes must be committed before final review. Shared implementation
and PR-comment completion runs authoritative read-only reconciliation after
normal sync. Completion resolves the logical effective base to a full locally
available object ID using its sync probe's remote-first branch order, including
remote-only PR bases; it never fetches inside reconciliation. The closing gate pins the reviewed tip and the permitted actual base-merge tip.
Direct late authored commits require fresh review and documentation. For clean
sync reuse, knowledge prose blocks and legacy history must be unchanged from
one parent, and typed history must be the exact immutable parent union. Concurrent
ambiguous edits, added knowledge in an amended merge, multiple merge bases, or
invalid snapshots require the normal fresh closing workflow. This comparison
covers knowledge/history, not every non-knowledge source-tree change.
Before and after every successful push, checks require a clean tree, repeat
reconciliation, and verify the
pinned tip. Push names the approved SHA explicitly, so a concurrent branch advance
cannot substitute a later commit. A recovery merge on that pinned path requires
fresh closing checks rather than re-keying an old review to a new candidate.
Failures clear the done marker and prevent PR finalization; a push already made
is not rolled back. This does not alter configured pass caps or sanctioned review
exceptions. No `done.*` option weakens the knowledge structural gate.
