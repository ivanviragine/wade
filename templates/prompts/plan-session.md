# Managed planning session

Read `{session_bundle}/WORKFLOW.md` first. It is authoritative for lifecycle,
safety, required steps, plan output, review, validation, and exit. Then read
each WORK methodology `SKILL.md` listed there. Read
`{session_bundle}/AVAILABLE_SKILLS.md` only when an additional project method would
help; availability does not activate a skill. If a skill conflicts with the
workflow, the workflow wins.

Inspect source at `{source_root}` (read-only reference if outside the planning
workspace; this does not grant additional write access).

Plan in the tool's native Plan mode using
`{session_bundle}/reference/plan-output-contract.md`. When
`{plan_dir}/interactive-session.json` exists, submit the reviewed plan through
`wade plan-session done {plan_dir} --from-file <native-plan-file>` or
`--from-stdin`. WADE writes its own plan files; use its review commands and revise
before completing and exiting the native CLI. Otherwise return one native
artifact for collection. The trusted parent process creates issues and draft
PRs only after validation. Never approve implementation or create tasks here.
