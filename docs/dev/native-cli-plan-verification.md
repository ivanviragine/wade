# Native CLI planning verification

Verified on 2026-09-16 with the published Crossby 0.32.1 wheel. Upstream approval
support shipped in [Crossby PR #181](https://github.com/ivanviragine/crossby/pull/181).
Codex terminal support subsequently shipped in Crossby 0.33.0 via
[PR #183](https://github.com/ivanviragine/crossby/pull/183); its WADE verification
is described below.

## Live WADE handoff

The opt-in probe in `scripts/probe_native_plan_handoff.py` launches the actual CLI
in native Plan mode using ordinary native approvals. It asks the model to plan a
small greeting change, execute readiness, submit an explicit plan, run the frozen
prompt-review method, acknowledge the performed review, and complete `done`.
The caller checks the handoff after normal native CLI exit. It creates no issues
or PRs. Every successful run retained the original greeting source unchanged.

| CLI/version | Result | Coverage |
|-------------|--------|----------|
| Claude Code 2.1.263 | Passed | Explicit native file import, self-review/ack, completion; fallback and real detached worktree with bootstrap/guards |
| Cursor 2026.09.10-fd3934a | Passed | Native plan file, import, self-review/ack, completion in fallback workspace |
| OpenCode 1.18.29 | Passed | Native Plan session, explicit handoff, self-review/ack, completion in fallback workspace |
| Antigravity CLI 1.2.3 | Passed | Native artifact file, import, self-review/ack, completion; fallback and real detached worktree with bootstrap/guards |
| Codex CLI 0.154.0 | Reviewed handoff verified | Published Crossby 0.33.0; ready-event prompt delivery; sandboxed detached worktree; native questions; stdin import, self-review/ack, completion |
| Copilot | Authenticated run omitted | Explicit user request after earlier credential failure; published adapter and WADE launch contracts remain tested |

Claude's native `plansDirectory` setting selects `.wade/plans/native/`. Cursor
and Antigravity chose session-owned files in their native storage. WADE imports
an explicitly supplied file or stdin artifact; it neither scans those stores nor
claims their paths can be chosen through a launch flag.

Native CLIs may offer to implement after composing a plan. Decline that offer,
complete WADE's workflow while still in Plan mode, then quit normally. Cursor
needed a native follow-up to finish the handoff after its initial build offer.
No implementation was approved. The probe identifies its development `wade`
executable explicitly because Cursor's login shell selected an older installed
copy despite the caller's PATH. This is test setup, not a production adapter.

These runs establish the native UI, command execution, and reviewed handoff;
they do not prove model compliance on every run or OS isolation. Completion and
parent validation reject missing, stale, or malformed handoffs. Upstream native
approval-mode verification is documented separately in Crossby; these WADE runs
used ordinary approvals, not unrestricted YOLO.

## Automated coverage and review

- Published-wheel Crossby focused planning contracts: 335 passed.
- Full WADE checklist: 4,392 tests, lint/format, and strict type checking passed;
  later focused regressions cover native policy rejection and incomplete-output
  preservation as well.
- Explicit host deterministic E2E lane: 109 passed, including the real completion
  command invoked by a fake native CLI and the explicit collector-compatibility lane.
- Fresh review fixed stale receipts after failed reviews, unsafe managed review
  sources, missing knowledge handoff at completion, native artifact containment,
  selected-tool bootstrap, and preservation before the first successful import.

Use `--worktree` on the probe for real bootstrap/guards. Keep CLI authentication
local and distinguish native startup, completed authenticated handoff, and
provider persistence; the live probe intentionally does not exercise the last.


## Codex deferred startup and stdin handoff

The Codex probe uses `--sandbox --worktree --model gpt-5.6-sol` and ordinary
native approvals. Crossby alone submits `/plan`; WADE receives `PLAN_READY`,
sends the rendered workflow prompt once, and requires `MESSAGE_SUBMITTED` before
accepting the handoff. User questions appear in the native Codex UI.

Codex ran readiness, inspected `greeting.py`, obtained native confirmation,
and piped its plan to `wade plan-session done --from-stdin`. WADE wrote `PLAN.md`
and rejected completion until review. Codex then ran `wade review plan`, read
and performed the frozen review method, acknowledged it with
`--ack-self-review`, and completed `done` while remaining in native Plan mode.
The parent returned `PASS: received 1 completed, reviewed plan(s)` after normal
Codex exit. Native session `01a0a7fa-90e6-7450-9a87-c0f12145da32` recorded Plan
mode throughout and one task message (Codex trims its final newline). The
completed receipt and exact plan content are checked again by the parent.
No issues or PRs are created by this probe; the greeting remains unchanged.

Published Crossby 0.33.0 wheel contracts: 374 passed, including the terminal PTY
suite, approval combinations, and collector regressions. WADE startup tests
reject absent, duplicate, mismatched, and out-of-order events. The older fake
app-server E2E lane now explicitly selects its collector capability, so it does
not claim to represent production Codex's new terminal path.

WADE adoption checklist: 4,408 tests passed, with lint, formatting, and strict
types passing. The live probe returned one completed reviewed plan after exit.
