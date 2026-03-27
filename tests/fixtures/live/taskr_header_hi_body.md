## Context / Problem

Add a tiny greeting header to `taskr` so we can exercise a minimal live WADE
implementation workflow against a real repo.

## Tasks

- [ ] Print `Hi` as the first line of output for every `taskr` command
- [ ] Keep the existing command behavior after that greeting header
- [ ] Cover at least the default no-args help path and one real subcommand path
- [ ] Add or update tests for the new header behavior

## Acceptance Criteria

- `uv run taskr` starts with `Hi` and still shows the existing help output
- `uv run taskr list` starts with `Hi` and still shows the list output
- `./scripts/test.sh` passes
