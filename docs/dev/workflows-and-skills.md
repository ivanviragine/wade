# Workflows and Dynamic Skills

WADE separates fixed lifecycle orchestration from replaceable reasoning methods.
This boundary is the core design rule:

```text
WADE workflow or bounded contract -> selected generic methodology -> result
```

The dependency never points back from a replaceable skill to WADE. A custom
skill can change how planning, implementation, or review is approached, but it
cannot remove review, documentation, synchronization, completion, or any other
fixed step.

## Ownership boundary

| WADE owns | Replaceable skills own |
|---|---|
| session step order and commands | methodology and heuristics |
| worktree/readiness/safety policy | domain knowledge |
| task and operation inputs | review rubrics |
| timeouts, pass caps, retry/skip policy | prioritization and reasoning approach |
| output/result contracts | suggestions within the fixed result contract |
| manifests, receipts, gates, provider mutations | no lifecycle state |

Lifecycle rules belong in `templates/workflows/`, service code, or deterministic
models. Replaceable defaults live in `templates/skills/` and are protected by an
architecture test that rejects WADE-specific lifecycle tokens.

## Sessions and delegations

`models/workflow.py` is the typed source of truth. `SessionKind` describes a
WADE-owned shell; `DelegationKind` describes one bounded AI operation. They are
not interchangeable with human PR labels, hook-specific phases, or `ai.*` config
keys; explicit mappings preserve those representations.

Interactive sessions:

| Session | WORK default | REVIEW default | Fixed workflow |
|---|---|---|---|
| `plan` | `builtin:planning` | `builtin:plan-review` | `plan.md` |
| `implementation` | `builtin:implementation` | `builtin:code-review` | `implementation.md` |
| `review-pr-comments` | `builtin:review-comments` | `builtin:code-review` | `review-pr-comments.md` |

The `deps` session owns detached-worktree readiness and recovery but is explicitly
non-interactive: it has no workflow template, launch prompt, SessionStart phase,
or session skill slots. Its AI reasoning is the always-foreign
`dependency-analysis` delegation.

Delegations:

| Delegation | Host behavior | Default method |
|---|---|---|
| `plan-review` | maps to a plan session's frozen REVIEW slot | `builtin:plan-review` |
| `code-review` | maps to implementation/review session REVIEW | `builtin:code-review` |
| `batch-review` | always foreign; no worktree is privileged | `builtin:batch-review` |
| `dependency-analysis` | always foreign, including inside planning | `builtin:dependency-analysis` |

Mapped reviews load the active session reviewer exactly. A CLI `--skill` cannot
replace that reviewer during the review command; refresh the session binding
explicitly. Foreign operations resolve independently and never inherit an
unrelated host slot.

## Fixed workflow rendering

Interactive bootstrap renders `.wade/session/WORKFLOW.md` from the session
definition and `templates/workflows/`. Rendering may inject deterministic facts:

- active WORK and REVIEW snapshot paths;
- whether review is enabled or an explicit configured skip;
- detected documentation targets and receipt command;
- shared interaction, review-budget, knowledge, and completion text.

It never reads methodology prose to decide workflow content. Disabled optional
review remains visible as a skipped step. Workflow references are copied under
`.wade/session/reference/`.

The launch prompt names the fixed workflow first, then the active WORK method,
task context, and `AVAILABLE_SKILLS.md`. It does not eagerly load REVIEW method
text. Tool-neutral repository-relative file instructions replace tool-specific
`@` expansion.

## Binding configuration

All new config is optional under schema version 2:

```yaml
skills:
  project:
    discover: true
    include: ["*"]
    exclude: []

sessions:
  implementation:
    skills:
      work: [project:domain-implementation]
      review: [project:security-review]
  plan:
    skills:
      work: [builtin:planning]
  review_pr_comments:
    skills:
      work: [builtin:review-comments]

delegations:
  plan_review:
    skills:
      work: [builtin:plan-review]
  code_review:
    skills:
      work: [project:security-review]
  batch_review:
    skills:
      work: [builtin:batch-review]
  dependency_analysis:
    skills:
      work: [builtin:dependency-analysis]
```

WADE applies `include` and `exclude` to candidate names, canonical references,
and source paths before inspecting skill contents. A filtered-out skill is not
part of the inventory and cannot block session startup because of invalid files;
selected skills still fail closed on unsafe paths, symlinks, or size limits.

Bindings are ordered lists. Supplying a CLI slot replaces its configured/default
list; it does not silently append. Users who want two methods list both in the
desired order. `sessions.deps` is invalid because deps exposes no session slot.

For a new session, precedence is:

1. CLI slot override;
2. `sessions.<session>.skills.<slot>`;
3. the matching delegation binding for a mapped REVIEW slot;
4. the built-in session default.

A standalone/foreign delegation uses CLI, then
`delegations.<delegation>.skills.work`, then its built-in default. When a session
and mapped delegation configure different reviewers, the session value wins for
that session and diagnostics report the shadowed candidate.

Session commands accept repeatable `--skill` and `--review-skill` flags. Bounded
review, batch, and deps commands accept repeatable `--skill`. Use `builtin:`,
`project:`, or an exact repository-relative `path:` ref.
`wade implement-batch` and the tracking-issue redirect preserve the ordered
implementation bindings by forwarding them to every child session.

## Frozen state and refresh

New interactive sessions write `.wade/session/manifest.json` with session and
workflow identity, task/AI identity, ordered resolved skills, source provenance,
materialized paths, content digests, and one order-sensitive composite digest per
slot. No absolute host path is persisted.

The composite digest is SHA-256 over canonical compact JSON containing each
component's position, canonical ref, and content digest. Reordering the same
skills changes the binding identity.

Resume uses the existing manifest and physical snapshots. Config or main-checkout
changes do not alter the active session. Resume-time overrides without
`--refresh-skills` fail; `--refresh-skills` or
`wade session refresh-skills` performs the explicit atomic replacement.
For a worktree-less planning fallback, both session commands resolve the bundle
from `WADE_PLAN_DIR`; refresh still loads config and discovers project skills
from the launch checkout and keeps absolute bundle paths in the rendered workflow.
Before reuse, WADE re-hashes the complete physical bundle (workflow, references,
support files, catalog, and skill snapshots) and revalidates every active skill
against its recorded file list and digest. Missing, edited, extra, symlinked, or
otherwise unsafe content fails closed until an explicit refresh replaces the bundle.

An implementation-to-PR-comment transition is a new session and replaces the
manifest/workflow. Durable review records survive outside the session bundle,
but apply only if the new session's review binding is identical.

## Bounded invocation contract

`skill_invocation_service.py` composes bounded prompts structurally in this
order:

1. authoritative operation contract;
2. selected method text in delimited method sections;
3. untrusted plan/diff/batch/task input in a delimited input section;
4. authoritative result contract.

No placeholder replacement runs across method or operation-input bytes. Each
method envelope names the exact absolute materialized root for that invocation,
so a skill may refer to copied resources even when bundle resolution and the
reviewer's repository working directory differ. Headless execution does not
depend on native tool discovery. The fixed service owns tool/model selection,
permissions, timeout, input collection, parsing, and side effects.

Foreign operations persist a narrower delegation manifest below
`.wade/operations/`. A successful synchronous operation removes its own bundle;
a recoverable failure may preserve it. Session refresh never touches operation
bundles.

## Binding-aware review state

Review attempts write one durable record per `(delegation, commit, binding)` under:

```text
.wade/reviews/review@<delegation>@<sha>@<binding-hex>.json
```

The record stores the ordered binding components and a fixed outcome:

| Outcome | Consumes pass | Satisfies review |
|---|---:|---:|
| `reviewed` | yes | yes |
| `no-diff` | no | yes |
| `timed-out` | yes | no |
| `nothing-staged` | no | no |

`no-diff` is valid only when committed branch, staged, and unstaged diffs are all
empty. An empty `--staged` index while other work exists records
`nothing-staged`, so input scoping cannot satisfy the gate accidentally.
Prompt mode emits the bounded review prompt but writes no satisfying record and
consumes no pass. After actually performing that self-review, the caller runs
`wade review implementation --ack-self-review`; that explicit second action
writes `reviewed` for the then-current commit and active binding. Successful
headless or interactive reviews write `reviewed` directly.

Records are idempotent and never downgrade a success. Pass caps count only the
active binding. Switching A to B preserves A's history but makes it inapplicable;
switching back to byte-identical A at the same commit reuses its record. Refresh
of only WORK does not invalidate a REVIEW record.

Manifest, review, and documentation-gate state uses descriptor-relative,
no-follow filesystem operations. Unsafe/malformed/unreadable state is absent for
gate purposes and fails toward re-review, never toward completion. SHA marker
files are never accepted as review evidence. The completion classifier also
revalidates the physical frozen workflow and skill bundle before it trusts the
manifest's REVIEW binding, any matching receipt, or the binding's pass count.
Tampering therefore cannot satisfy either the exact-review fast path or the
implementation pass cap; refresh the bundle and review again.

## Completion-policy alignment

Implementation and PR-comment workflows require a documentation-impact decision
for the current commit:

```text
wade implementation-session docs --updated
wade implementation-session docs --not-needed "reason"
wade review-pr-comments-session docs --updated
wade review-pr-comments-session docs --not-needed "reason"
```

`done` verifies the receipt rather than pretending to judge documentation
quality. This decision has no config disable switch: `--not-needed "reason"` is
the explicit negative outcome, preserving the mandatory workflow step without
claiming every change needs a documentation edit. PR-comment `done` also aligns
with its fixed workflow by gating the PR
summary, synchronization, conventional title, resolved threads, current-binding
review, documentation decision, and knowledge validity according to project
policy.

Planning remains independently strict: the parent service always parses and
validates produced plan files, including salvage paths. `plan-session done` is
helpful session telemetry, not authority over parent consumption.

## Extension rules

To change a lifecycle:

1. update the typed session/delegation definition when identity or stable steps
   change;
2. update the fixed workflow/delegation contract and deterministic service gate;
3. preserve explicit disabled-state rendering and resume semantics;
4. test that a custom skill cannot change the fixed behavior.

To add or change a replaceable default:

1. create/update `templates/skills/<method>/SKILL.md` and any local resources;
2. keep it generic and free of WADE lifecycle vocabulary;
3. add it to the built-in methodology catalog, not the support installer;
4. evaluate the method on representative tasks and keep its context surface
   within budget;
5. test custom and default resolution through the same contract.

Do not add an arbitrary workflow DSL. Session step order and supported slots
remain explicit typed WADE definitions.

## Default-method evaluation record

The newly authored planning and implementation defaults were applied to two
representative WADE tasks during this redesign and reviewed against the fixed
workflow contract:

| Method | Recorded task | Quality evidence | Correction from evaluation |
|---|---|---|---|
| `builtin:planning` | Decouple lifecycle orchestration, dynamic methods, discovery, frozen state, and support projection in one cohesive redesign | The resulting plan separated facts and invariants, identified typed ownership boundaries, ordered implementation phases, failure cases, context budgets, and unit/integration/E2E contracts instead of reducing the work to template moves | Review feedback exposed ambiguous precedence and receipt semantics; those became explicit mappings and binding-aware records rather than skill prose |
| `builtin:implementation` | Make an unknown implementation skill fail before draft-PR, task-label, or project-board mutation | The method translated the goal into an observable negative contract, traced worktree creation through bootstrap and PR retargeting, preserved branch-specific discovery, and produced focused unit plus real CLI coverage before the full suite | The first implementation order still allowed PR mutation before composition; final review caught it and moved immutable bundle preflight ahead of every provider write |

This is a qualitative, repository-evidence review rather than a claim that a
particular model will always produce identical output. The defaults provide the
missing generic method; fixed workflows, deterministic gates, and tests remain
the authority when output quality varies.
