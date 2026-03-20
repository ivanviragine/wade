## Context / Problem

Follow up on the existing taskr greeting header and change it so we
exercise a second real live WADE implementation workflow on top of a prior
merge.

## Tasks

- [ ] Change the first-line greeting header from `Hi` to `Howdy`
- [ ] Keep the existing command behavior after that header
- [ ] Update tests to match the new greeting

## Acceptance Criteria

- `uv run taskr` starts with `Howdy` and still shows the existing help output
- `uv run taskr list` starts with `Howdy` and still shows the list output
- `./scripts/test.sh` passes
