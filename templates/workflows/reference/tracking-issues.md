# Tracking and child tasks

When the current task belongs to a tracking task, `done` detects the parent and
adds `Part of #<parent>` alongside `Closes #<child>`; no manual PR-body mutation
is needed. `--no-close` deliberately leaves the child open.

After merge, use the configured provider workflow to read and update the parent
checklist (`wade task read` / `wade task update`) rather than a provider-specific
API. When every child is complete, close the parent with `wade task close`.

When implementing the tracking task itself, summarize child status in
`PR-SUMMARY.md` and use task-list syntax where the provider supports it.
