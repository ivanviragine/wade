# Extending ghaiw

Step-by-step guides for common extension tasks. For architecture context, see `docs/dev/architecture.md`.

## Adding a New Subcommand to `task`

The task CLI is in `src/ghaiw/cli/task.py`, business logic in `src/ghaiw/services/task_service.py`. When adding a new subcommand:

1. **Implement the service method** — Add the business logic in `task_service.py` (or a new service if warranted)
2. **Wire the CLI** — Add a Typer command function in `cli/task.py` with appropriate options/arguments
3. **Add models** — If the subcommand introduces new data types, add them to `models/task.py`
4. **Update help** — Typer generates help automatically from docstrings and `help=` parameters
5. **Update docs** — README.md (user-facing), skill files (agent-facing)

## Adding a New AI Tool

Thanks to `__init_subclass__` auto-registration, adding a new AI tool requires only one file:

1. Create `src/ghaiw/ai_tools/<tool_name>.py`
2. Define a class that inherits from `AbstractAITool` and sets `TOOL_ID`
3. Implement the required abstract methods: `capabilities()`, `get_models()`, `launch()`, `parse_transcript()`
4. Optionally override `is_model_compatible()`, `plan_mode_args()`, `normalize_model_format()`
5. Add the tool ID to `AIToolID` enum in `models/ai.py`
6. Import the module in `ai_tools/__init__.py` to trigger registration

No modification to `base.py`, services, or CLI is needed.

## Adding a New Provider

The provider system uses `AbstractTaskProvider` ABC (`src/ghaiw/providers/base.py`) with `GitHubProvider` as the current implementation. Unlike AI tools (which use `__init_subclass__` auto-registration), providers use a simple factory function. To add a new provider (e.g., Linear, Jira):

1. Create `src/ghaiw/providers/<provider_name>.py`
2. Implement all abstract methods from `AbstractTaskProvider`
3. Add the provider ID to `ProviderID` enum in `models/config.py`
4. Add an `elif` branch in `providers/registry.py:get_provider()` to return the new provider

## Version Bumping

Version lives in `src/ghaiw/__init__.py` (`__version__`) and `pyproject.toml` (`version`). Use `scripts/auto_version.py` to bump it:

```bash
python scripts/auto_version.py patch           # bug fixes, docs (0.1.0 -> 0.1.1)
python scripts/auto_version.py minor           # new features, flags (0.1.0 -> 0.2.0)
python scripts/auto_version.py major           # breaking changes (0.1.0 -> 1.0.0)
python scripts/auto_version.py minor --dry-run # preview only
```

The script updates both files, generates `CHANGELOG.md`, commits, and creates an annotated git tag.

### Changelog Generation

`scripts/changelog.py` generates `CHANGELOG.md` from the full git history. It groups commits by conventional-commit type (Features, Bug Fixes, etc.) under version-tagged sections. It runs automatically as part of `auto_version.py`, or standalone:

```bash
python scripts/changelog.py               # write CHANGELOG.md
python scripts/changelog.py --stdout      # print to stdout
python scripts/changelog.py --tag v1.0.0  # label unreleased as v1.0.0
```

### Semver Rules

- **patch** — bug fixes, documentation, refactors with no behavior change
- **minor** — new features, new commands, new flags (backward compatible)
- **major** — breaking changes: removed commands, renamed flags, changed output format
