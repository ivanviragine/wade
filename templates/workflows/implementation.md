# Implementation workflow

This file is the authoritative lifecycle and safety contract for the
implementation session. Methodology skills control approach, never lifecycle,
safety, review receipt, synchronization, or completion. If instructions
conflict, this workflow wins.

Active WORK methodology:
{work_skill_list}

Review methodology (loaded only by the bounded review step):
{review_skill_list}

{interaction_policy}

## Required steps

1. **Check readiness.** Run `wade implementation-session check` first. Proceed
   only on `IN_WORKTREE`. Do not edit on `IN_MAIN_CHECKOUT`, with blocked Git
   metadata, or without the required `gh` capability; follow the command's
   narrow `reason=...` remediation.
2. **Catch up.** Startup normally synchronizes the branch. If recovery is
   required, use `reference/recovery.md` and WADE lifecycle commands rather
   than reimplementing Git orchestration.
3. **Understand the task.** Read `PLAN.md`, the current code, relevant tests and
   project instructions. Challenge the plan where repository evidence differs.
   Load `reference/tracking-issues.md` when this is a parent/child task, and
   `reference/new-plan.md` only if a separate plan is finalized mid-session.
4. **Apply the WORK methodology.** Read every listed WORK skill and its local
   resources. Implement only the task scope in this worktree. Use Conventional
   Commits; never create a PR or push directly.
5. **Verify.** Run the repository-prescribed focused and full checks appropriate
   to the risk. Fix failures caused by the change and distinguish unrelated
   baseline failures with evidence.
6. **Method review.** {review_step_state}
7. **Documentation [mandatory decision].** {documentation_step}
8. **Knowledge.** {knowledge_step}
9. **PR summary.** Write or update `PR-SUMMARY.md` using
   `reference/pr-summary-format.md`; never commit this session artifact.
10. **Sync.** Re-run the readiness check after any resume or permission change,
    then run `wade implementation-session sync --json`. Resolve reported
    conflicts through the documented recovery path and repeat until exit 0.
11. **Done.** Run `wade implementation-session done`. This deterministic gate
    verifies required artifacts and the binding-aware review result, syncs when
    needed, pushes, and updates the PR. Fix gate failures; do not bypass them
    except through an explicit configured policy or an auditable sanctioned
    review skip.
12. **Present results.** {completion}

Never modify generated session bundles under `.wade/session/` or tool-native
skill roots. Use `wade task create`, never direct issue-creation APIs.

{review_budget}
