# Contributing to WADE

## Development Setup

**Prerequisites:** [gh CLI](https://cli.github.com/) must be installed and authenticated — WADE shells out to it for all GitHub operations.

```bash
gh auth login     # first-time setup
gh auth status    # verify it worked
```

Then clone and install:

```bash
git clone https://github.com/ivanviragine/wade.git
cd wade
uv pip install -e ".[dev]"
```

`uv.lock` is tracked for reproducible dependency validation. When adopting a
Crossby release, update the declaration, lockfile and contract tripwire together;
use `uv sync --all-extras` to verify the installed release without a local override.

## Running Checks

> Always use the scripts — never invoke `pytest`, `mypy`, or `ruff` directly.

| Command | What it does |
|---------|-------------|
| `./scripts/test.sh` | Run all tests (excludes live) |
| `./scripts/test.sh tests/unit/` | Unit tests only |
| `./scripts/check.sh` | Lint + type-check |
| `./scripts/check.sh --lint` | Lint only |
| `./scripts/check.sh --types` | mypy strict only |
| `./scripts/fmt.sh [paths...]` | Auto-format selected paths (default: `src/`) |
| `./scripts/check-all.sh` | Full suite (tests + lint + types) |

## Architecture

```
CLI Layer      →  services, models, config, logging, ui
Service Layer  →  providers, crossby (AI tool adapters), git, models, config, logging
Provider Layer →  models, config, logging  (no service imports)
Git Layer      →  models, config, logging  (no service imports)
Models Layer   →  nothing (leaf)
```

Never import a higher layer from a lower one, and keep business logic out of `cli/`. One sanctioned exception runs the other way: services may import the lower `skills/` utility layer for skill-file management (`install_skills`, the `*_SKILLS` registries, gitignore/cross-tool constants), never the reverse.

AI tool adapters live in the external [`crossby`](https://github.com/ivanviragine/crossby) package, not this repo — wade's services import `crossby.ai_tools` and `crossby.models.ai` directly. See `docs/dev/architecture.md`.

CLI modules are thin dispatch — parse flags with Typer, call service methods. Business logic lives in `services/`, not `cli/`.

See `docs/dev/architecture.md` for the full reference.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Semver bump |
|--------|------------|
| `feat:` | minor |
| `feat!:` | major (breaking) |
| `fix:`, `docs:`, `refactor:`, `chore:`, `test:` | patch |

## Releasing

### Every release

Merged PRs run CI and do not bump versions. Choose a release for a batch of
changes using the [deliberate release process](docs/dev/releases.md), including
its fresh-clone prerequisite so unpublished local version tags cannot affect
the changelog. Confirm repository Actions and the draft-release workflow are
enabled before pushing; a paused tag-push event will not replay later. In that
fresh release clone, use the version script explicitly when ready to ship:

```bash
uv run python scripts/auto_version.py patch   # or minor / major
release_tag="$(git describe --tags --exact-match HEAD)"
git push --atomic origin HEAD:main "refs/tags/$release_tag"
```

This bumps the source and manifest versions, runs `uv lock` against the script's
project root, and includes the synchronized `uv.lock` with `CHANGELOG.md` in the
release commit before tagging. The explicit atomic push sends that commit and
only its selected tag. Compatible dependency resolutions
are retained; no upgrade flags are used. The lock child process clears inherited
`UV_FROZEN` so frozen installation preferences cannot silently skip the refresh;
the caller's environment is unchanged. uv must be on PATH, including for direct
Python and pre-push-hook callers. A lock failure stops before staging, committing,
tagging or pushing and leaves any partial edits visible for inspection. Unrelated
staged paths are rejected before release edits; unrelated unstaged edits are
preserved and can still prevent the later pull/rebase.

CI then:

1. Creates a **draft GitHub Release** with the changelog notes
2. You review it on GitHub and click **Publish Release**
3. CI publishes the wheel to PyPI automatically, authenticated via [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC) — no API tokens stored anywhere. If PyPI ever rejects the publish with `invalid-publisher`, the trusted publisher for `wade-cli` needs to be (re-)registered on pypi.org under **Publishing** settings, matching `ivanviragine/wade`, workflow `publish.yml`, environment `pypi`.

The legacy local `pre-push` hook described below can still auto-version direct
pushes to `main`. Do not install that optional hook when using deliberate
batch releases; an already-installed copy must be removed explicitly. This
does not affect WADE's separate worktree quality and guard hooks.

### Git Hooks

```bash
./scripts/install-hooks.sh          # install into .git/hooks/
./scripts/install-hooks.sh --force  # overwrite existing hooks
```

Installs `pre-push` from `scripts/hooks/pre-push`, which detects conventional-commit prefixes on pushes to `main`/`master` and runs the version-bump step above automatically (skipped if the tip commit is already a version bump, to avoid double-bumping).

### Version bump types

```bash
uv run python scripts/auto_version.py patch   # bug fixes     0.1.0 → 0.1.1
uv run python scripts/auto_version.py minor   # new features  0.1.0 → 0.2.0
uv run python scripts/auto_version.py major   # breaking      0.1.0 → 1.0.0
```

For a preview without script writes or commands, run
`python3 scripts/auto_version.py patch --dry-run`. An outer `uv run` can synchronize
a stale lock before Python starts, even when the script receives `--dry-run`.

uv may normalize the lock schema: uv 0.13.1 changes revision 3 to 5 without
changing dependency resolutions. The resulting coherent lock was verified with
`uv sync --all-extras --locked` under both uv 0.9.27 and 0.13.1. No global uv
upgrade is required for this repair.

If a deliberate bump fails while synchronizing the lock, stop and inspect the
working tree. Resolve the lock problem before rerunning the version script;
do not publish a tag with mismatched source and lock versions. There is no
per-merge bump workflow to rerun. Verify the release commit, tag, CI, and draft
before publishing it.

## Detailed Reference

| Topic | File |
|-------|------|
| Architecture, config, commands | `docs/dev/architecture.md` |
| Adding AI tools, providers, subcommands | `docs/dev/extending.md` |
| Writing and running tests | `docs/dev/testing.md` |
| Skills system and `wade init` | `docs/dev/skills-system.md` |
| Documentation policies | `docs/dev/documentation-policies.md` |
