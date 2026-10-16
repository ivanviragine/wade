# Tracking and child tasks

When the current task belongs to a tracking task, `done` detects the parent and
adds a parent line alongside the child's reference; no manual PR-body mutation
is needed. GitHub tasks get `Part of #<parent>` and `Closes #<child>`; other
providers get qualified references such as `Part of clickup:<parent>` and
`Completes clickup:<child>` (never a GitHub closing keyword), and WADE closes
the child through its provider after a WADE-driven merge. `--no-close`
deliberately leaves the child open.

After merge, use the configured provider workflow to read and update the parent
checklist (`wade task read <id>` / `wade task update <id>`) rather than a
provider-specific API, keeping each item's exact reference (`#<number>` or
`clickup:<id>`). When every child is complete, close the parent with
`wade task close <id>`.

When implementing the tracking task itself, summarize child status in
`PR-SUMMARY.md` and use task-list syntax where the provider supports it.
