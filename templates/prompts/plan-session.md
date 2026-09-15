# Managed planning session

Read `{session_bundle}/WORKFLOW.md` first. It is authoritative for lifecycle,
safety, required steps, plan output, review, validation, and exit. Then read
each WORK methodology `SKILL.md` listed there. Read
`{session_bundle}/AVAILABLE_SKILLS.md` only when an additional project method would
help; availability does not activate a skill. If a skill conflicts with the
workflow, the workflow wins.

Inspect source at `{source_root}` (read-only reference if outside the planning
workspace; this does not grant additional write access).

Plan the requested feature in the harness's native Plan mode. Return one native
Markdown artifact using `{session_bundle}/reference/plan-output-contract.md`.
The trusted parent process imports its members under `{plan_dir}`, runs the fixed
review and validation, and creates the issue(s) and draft PR(s). Do not write WADE
plan files, create tasks, approve implementation, or run completion commands.
