# Creating tasks from a new plan

If a new plan or feature specification is finalized during implementation, keep
it in the current worktree and create tasks through `wade task create`; never use
a provider-specific issue-creation API. Report the created task handles and name
`wade implement <id>` as the human's next command. Do not start another work
session from inside the current one.
