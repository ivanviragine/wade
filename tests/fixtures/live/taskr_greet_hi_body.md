## Context / Problem

Add a tiny greeting command to `taskr` so we can exercise a minimal live WADE
implementation workflow against a real repo.

## Tasks

- [ ] Add a `greet` command to `taskr`
- [ ] `taskr greet` prints exactly `Hi`
- [ ] Update the CLI help output to mention `greet`
- [ ] Add or update tests for the new command

## Acceptance Criteria

- `python -m taskr.cli greet` prints exactly `Hi`
- Existing taskr commands still work
- `./scripts/test.sh` passes
