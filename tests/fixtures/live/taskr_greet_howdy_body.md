## Context / Problem

Follow up on the existing `taskr greet` command and change its greeting so we
exercise a second real live WADE implementation workflow on top of a prior
merge.

## Tasks

- [ ] Update `taskr greet` to print exactly `Howdy`
- [ ] Update the help output if needed
- [ ] Update tests to match the new greeting

## Acceptance Criteria

- `python -m taskr.cli greet` prints exactly `Howdy`
- `./scripts/test.sh` passes
