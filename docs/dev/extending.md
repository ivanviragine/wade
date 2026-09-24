# Extending WADE

Step-by-step guides for common extension tasks. For architecture context, see `docs/dev/architecture.md`.

## Adding a New Subcommand to `task`

The task CLI is in `src/wade/cli/task.py`, business logic in `src/wade/services/task_service.py`. When adding a new subcommand:

1. **Implement the service method** — Add the business logic in `task_service.py` (or a new service if warranted)
2. **Wire the CLI** — Add a Typer command function in `cli/task.py` with appropriate options/arguments
3. **Add models** — If the subcommand introduces new data types, add them to `models/task.py`
4. **Update help** — Typer generates help automatically from docstrings and `help=` parameters
5. **Update docs** — README.md (user-facing), skill files (agent-facing)

## Adding a New AI Tool

AI tool adapters (`AbstractAITool` subclasses, using `__init_subclass__` auto-registration) live in the external [`crossby`](https://github.com/ivanviragine/crossby) package, not in this repo — see `docs/dev/architecture.md` for the full list of what moved there. Adding a new AI tool means adding an adapter in crossby, then in wade: bump the `crossby` pin in `pyproject.toml`, and add the tool's binary name to `README.md`'s "Supported AI Tools" table. No changes to wade's `services/` or `cli/` are needed unless the tool needs command-specific handling.

### Native planning collectors

For **planning collectors**, activation-only `supports_plan_mode` is insufficient.
Adopt a published Crossby release with complete-session metadata, public
`preflight_plan_session`, `run_plan_session`, and supported `PlanCommandPolicy`.
Update `pyproject.toml`, the tracked `uv.lock`, and the Crossby contract tripwire
together, then verify without local sources or PR overrides. Do not manually
bump WADE's version. Temporary isolated development must record the full SHA.
WADE must not add tool-specific flags, protocol parsing, private collectors,
storage scraping, or an editing-mode fallback to make a collector eligible.

Extend tests at the public request/result/interaction boundary, plus deterministic
CLI handoff tests. An artifact source need not support a requested output path:
WADE imports returned Markdown using the same explicit one/multi-plan contract.
Validate native regressions against the adopted release (see
[testing](testing.md#native-planning-dependency-validation)); do not equate mocked
success with installed/authenticated all-tool success.

## Adding a New Provider

The provider system uses `AbstractTaskProvider` ABC (`src/wade/providers/base.py`) with `GitHubProvider`, `ClickUpProvider`, and `MarkdownIssueProvider` as current implementations. Unlike AI tools (which are external, via crossby), providers are local to wade and use a registry pattern. To add a new provider (e.g., Linear, Jira):

1. Create `src/wade/providers/<provider_name>.py`
2. Implement all abstract methods from `AbstractTaskProvider`
3. Add the provider ID to `ProviderID` enum in `models/config.py`
4. Register the provider in `providers/__init__.py` via `register_provider(ProviderID.YOUR_ID, YourProvider)` (use a lazy loader for optional dependencies)

Providers that support planning handoff recovery must also implement
`find_tasks_by_body_marker`. It must exhaustively search the provider's open
tasks for the exact durable marker; a bounded task-list page can miss a task
created before a local recovery-progress write failed, causing a duplicate.

Non-GitHub providers (ClickUp, Markdown) still need PRs and PR-review APIs, which are GitHub-only. They get these by composing `GitHubPRDelegateMixin` (`providers/_pr_delegate.py`), which routes PR/review calls through `gh` while task CRUD stays on the provider's own backend.

## Version Bumping

Version lives in `src/wade/__init__.py` (`__version__`) and `pyproject.toml` (`version`). Use `scripts/auto_version.py` to bump it:

```bash
uv run python scripts/auto_version.py patch           # bug fixes, docs (0.1.0 -> 0.1.1)
uv run python scripts/auto_version.py minor           # new features, flags (0.1.0 -> 0.2.0)
uv run python scripts/auto_version.py major           # breaking changes (0.1.0 -> 1.0.0)
uv run python scripts/auto_version.py minor --dry-run # preview only
```

The script updates both files, generates `CHANGELOG.md`, commits, and creates an annotated git tag.

### Changelog Generation

`scripts/changelog.py` generates `CHANGELOG.md` from the full git history. It groups commits by conventional-commit type (Features, Bug Fixes, etc.) under version-tagged sections. It runs automatically as part of `auto_version.py`, or standalone:

```bash
uv run python scripts/changelog.py               # write CHANGELOG.md
uv run python scripts/changelog.py --stdout      # print to stdout
uv run python scripts/changelog.py --tag v1.0.0  # label unreleased as v1.0.0
```

Breaking changes lead each version in their own **Breaking Changes** section, and
are *also* listed under their own type below it so a reader skimming "Features"
still sees them. A commit qualifies either way Conventional Commits allows — a
`!` subject marker (`feat!:`) or a `BREAKING CHANGE:` footer under an ordinary
subject — matching what the `commit_msg` hook accepts. When a footer is present
its text is indented under the entry, because the footer is what carries the
migration path. The footer runs to the first blank line **or the next footer
token** (`Refs:`, `Signed-off-by:`, `Closes #1`), so trailers written directly
underneath it stay out of the release notes; wrap freely, but start a new
paragraph before any prose that should not be published.

### Semver Rules

> The bump itself does **not** come from these commits. `auto-version.yml` reads
> only `github.event.pull_request.title`, and `done` syncs that title from the
> **issue** title — so a breaking change needs `feat!:` on the *issue*, or the
> release ships as a minor.

- **patch** — bug fixes, documentation, refactors with no behavior change
- **minor** — new features, new commands, new flags (backward compatible)
- **major** — breaking changes: removed commands, renamed flags, changed output format
