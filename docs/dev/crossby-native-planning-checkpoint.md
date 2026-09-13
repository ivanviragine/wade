# Crossby native planning dependency checkpoint — #511

Inspected on 2026-09-13. This records the public-contract checkpoint required by
issue #511; it is not an implementation-completion or release-acceptance report.

## Exact dependency

- Crossby PR: [#177](https://github.com/ivanviragine/crossby/pull/177), open and
  unmerged when checked.
- Latest inspected PR head:
  [`c6829287c816d2dbd8b4f47dfdaceae41f0722a3`](https://github.com/ivanviragine/crossby/tree/c6829287c816d2dbd8b4f47dfdaceae41f0722a3).
- Latest published release when checked:
  [`v0.30.2`](https://github.com/ivanviragine/crossby/releases/tag/v0.30.2).
  That release has activation-only planning and does not contain
  `run_plan_session`. The PR also reports package version `0.30.2`, so a version
  string alone cannot distinguish the development build from the release.
- Development install: an isolated virtual environment at
  `/tmp/wade-511-env.Tl7zQJ`, installed from the full PR SHA. Its distribution
  `direct_url.json` records that exact commit. The inspection checkout is
  `/tmp/wade-crossby-511.BDRNpF` at the same SHA.
- WADE's dependency declaration remains `crossby>=0.29.0,<0.30.0`; its ordinary
  environment currently resolves `0.29.1`. No release number has been guessed,
  and no override has been added to WADE's dependency declaration or lockfile.

The development environment intentionally overrides WADE's existing constraint.
A separate virtual project at `/tmp/wade-511-project.SWtDxO` depends on
`wade-cli[dev]`, sets `tool.uv.package = false`, points `tool.uv.sources.wade-cli`
to this worktree as an editable path, and pins:

```toml
[tool.uv]
package = false
override-dependencies = [
  "crossby @ git+https://github.com/ivanviragine/crossby.git@c6829287c816d2dbd8b4f47dfdaceae41f0722a3",
]
```

For the stable WADE compatibility runs, all three variables are set:

```bash
UV_PROJECT=/tmp/wade-511-project.SWtDxO \
UV_PROJECT_ENVIRONMENT=/tmp/wade-511-env.Tl7zQJ \
UV_NO_SYNC=1 ./scripts/test.sh
```

Use the same environment for `./scripts/test-e2e.sh` and `./scripts/check.sh`.
`UV_NO_SYNC=1` alone is insufficient: the full suite exercises worktree bootstrap,
whose configured setup hook calls `uv sync --all-extras` directly. An initial
run replaced the PR package with the ordinary `0.29.1` dependency partway
through testing. The separate `UV_PROJECT` pins that nested sync too. The
initial mixed-version run is not accepted as exact-SHA verification.

## Required upstream facility: command preauthorization

WADE defines `WADE_BASE_ALLOWLIST_PATTERN = "wade *"` as a required
preauthorization in `src/wade/models/config.py`. `PermissionsConfig` also exposes
project-specific `allowed_commands`. The current planning runner passes those
patterns to the launch API; bootstrap additionally uses Crossby's public
Claude and Cursor project allowlist writers.

The inspected public
[`PlanSessionRequest`](https://github.com/ivanviragine/crossby/blob/c6829287c816d2dbd8b4f47dfdaceae41f0722a3/src/crossby/models/ai.py#L303)
accepts exactly:

```text
prompt, working_dir, model, effort, trusted_dirs, plan_output_dir,
sandbox, network_access, approval_policy, timeout_seconds
```

It forbids unknown fields. There is no `allowed_commands`, execution-context
object, or public collected-session command-policy facility. The existing
`allowed_commands_args()` belongs to command construction for ordinary launches;
there is no supported way to attach its arguments to `run_plan_session()`.
The available public project allowlist writers cover Claude and Cursor, not
Codex, OpenCode, or Antigravity CLI.

An interaction bridge cannot fill this gap faithfully.
[`PlanInteraction`](https://github.com/ivanviragine/crossby/blob/c6829287c816d2dbd8b4f47dfdaceae41f0722a3/src/crossby/models/ai.py#L255)
contains display text, question/options, selection constraints, and provenance,
but no structured command, working directory, or requested permission target.
For example, Codex's callback may contain only the generic prompt
"Codex requests permission during planning." Matching a command allowlist
against this prose cannot establish which operation would be approved.

Before adopting the affected collectors, Crossby needs a public, tested route
for scoped command preauthorization, with metadata/validation that states which
collectors preserve or reject it. Possible contract designs include a request
policy or an execution-context facility. A callback-based route would also need
authoritative structured operation details sufficient to apply the policy.
These are requirements for the upstream contract, not proposed WADE request
fields or permission to implement native adapter logic here.

Under #511's requirement to retain required command policy, this is a dependency
blocker. Dropping `wade *`, silently ignoring configured patterns, using blanket
approval, or allowing only collectors with existing project writers would not
satisfy the requested integration. No planning runner or workflow has been
changed while this requirement remains unresolved.

## Other compatibility decisions

| Requirement | Evidence and WADE integration consequence |
|---|---|
| Early tool/version check | Public `supports_plan_session`, `plan_mode.verified_version`, `detect_binary_version_info()`, and `parse_semver()` support a metadata-driven check after final selection and before worktree/provider effects. There is no public complete-request preflight method; `validate_plan_mode_request()` is activation-only. Do not describe metadata checks as runtime model/protocol validation. |
| Native collection | Claude, Codex, Cursor, OpenCode, and Antigravity CLI declare complete collectors. Copilot explicitly declares collection unsupported; its verified headless transport cannot expose native questions. VS Code and Antigravity IDE are unsupported. Eligibility must follow current metadata, not a WADE tool allowlist. |
| Output routing | `result.plan` is authoritative. Only `REQUESTED_PATH` collection accepts `plan_output_dir`, which must be contained in `working_dir`. Claude creates a fresh child directory; its native source must not become a second imported task. |
| Policy dimensions | Request sandbox/network/approval/trusted directories are separate from native mode and WADE's post-plan YOLO behavior. Tool-managed policies do not establish explicit sandbox confinement. WADE must check explicit requirements against metadata and retain runtime rejection. |
| Interaction | Native options, multi-select, free text, denial, skip, cancellation, and separate plan approval have public models. Callback waits must be independently cancellable/bounded because Crossby executes them on daemon threads. Noninteractive WADE prompts cannot supply defaults as native answers. |
| Scenes/environment | WADE's current planning runner does not accept a scene or call `build_launch_environment()`. Its fallback `WADE_PLAN_DIR` uses process-environment inheritance. The collected request has no scene or environment field; any new requirement for a scoped scene/environment needs a public upstream route and verification. Existing copied project files and inherited environment must be checked, not assumed equivalent to scene application. |
| Transcript/usage | The result has no transcript or usage fields. Preserve its exact provenance separately; do not create a transcript from `result.plan` or fabricate zero-valued usage. A session ID must be accounted for independently of token usage. |
| Multiple plans | A native artifact is not a task boundary. A deterministic, explicitly versioned bundle envelope is still needed for multiple WADE plans, including filenames and relationships, with collision/path/member validation and explicit valid-subset decisions. |
| Fixed lifecycle | `templates/workflows/plan.md` currently requires file writing, user review, method review, knowledge, and `plan-session done` in the child. Artifact import requires an explicit parent-side adaptation, including the frozen REVIEW binding and vote handoff. None of these steps may disappear when collection returns. |
| Failure/salvage | Public errors expose typed failure categories and available IDs/paths. They do not promise a validated partial `plan`. Preserve recoverable artifacts for inspection without treating failure paths or transcript text as successful collected output. |
| Timeout | `timeout_seconds` has one collection deadline and a 3,600-second maximum. WADE must preserve configured supported values or reject unsupported ones; timeout remains a transport failure, not successful collection. |

## Verification at this checkpoint

All commands below use the isolated exact-SHA environment. Upstream tests run
in the Crossby inspection checkout using its own `./scripts/test.sh`; WADE
checks run in the #511 worktree using WADE's prescribed scripts.

- Crossby focused suite: **303 passed** across
  `test_plan_sessions.py`, `test_plan_permission_responses.py`,
  `test_opencode_plan_server.py`, and `test_plan_mode_contract.py`.
  This covers conflicting text/option permission responses, explicit denial,
  unsupported Copilot collection, unavailable/disabled effort variants,
  authoritative terminal export selection, and timeout/process cleanup.
- Crossby native local checks: **12 passed** in
  `test_codex_plan_policy.py` and `test_opencode_plan_http.py`, with
  `CROSSBY_CODEX_LOCAL_SMOKE=1 CROSSBY_OPENCODE_LOCAL_SMOKE=1`. Codex starts the
  real app-server and verifies the applied sandbox policy, then stops before
  model inference. OpenCode uses its real native server and a deterministic
  local model to exercise progress, native questions, multi-select answers,
  exact export, effort variants, and blocked-callback cleanup. These do not
  establish authenticated model success for every harness or a WADE lifecycle
  handoff.
- WADE `./scripts/check.sh`: **passed** (lint, format, strict types).
- WADE `./scripts/test-e2e.sh`: **101 passed, 1 failed**. The existing fake
  Claude binary in `test_plan_contract.py` does not report the version now
  required by Crossby's activation contract. Its fixture and the planning
  handoff must be migrated when implementing #511.
- WADE `./scripts/test.sh`: **4,304 passed, 5 failed**. Besides the E2E fixture
  above, three tests in `test_plan_service.py` / `test_yolo.py` still require
  the obsolete behavior where YOLO displaces plan mode. Crossby now rejects
  that combination with `PlanModeConflictError`. The fifth failure is the
  existing exact-version tripwire. These expectations must be updated as part
  of the actual integration and published-dependency adoption, not removed to
  make an unimplemented migration appear green.

The PR distribution's `direct_url.json` was checked during and after the stable
rerun; it retained the full inspected commit. The stable override also kept
WADE installed from this #511 worktree, despite nested bootstrap installation.

The ordinary environment's version tripwire already fails independently:
`test_crossby_version_and_skill_root_mapping_contract` expects `0.29.0`, while
the existing environment contains `0.29.1`. A targeted ordinary-environment
check also confirms that the existing planning E2E test passes there. No
baseline assertions have been changed to conceal dependency incompatibilities.

Detected CLI versions: Claude `2.1.263 (Claude Code)`, Codex
`codex-cli 0.154.0`, Cursor `2026.09.02-c22c1a3`, OpenCode `1.18.29`,
Copilot `GitHub Copilot CLI 1.0.83.`, Antigravity CLI `1.2.2`, VS Code
`1.136.1`. Detection alone is not native plan-session success.

## Resume and final acceptance

Resolve the command-policy contract above, then re-check the latest full PR SHA
and public API before implementing the WADE collector path. Keep policy,
interaction, artifact import, fixed review/knowledge/validation, and task
persistence in one cohesive integration. The existing launch-only planning
implementation remains unfixed at this checkpoint.

Final acceptance additionally requires the merged, published Crossby release,
an updated WADE dependency/lockfile/contract tripwire, verification without the
override, and the managed implementation documentation, review, synchronization,
and completion gates. No published version or all-tool native success is claimed.

WADE currently ignores `uv.lock` in `.gitignore`; final adoption must explicitly
resolve the issue's lockfile-delivery requirement rather than assume the locally
generated lockfile is tracked.
