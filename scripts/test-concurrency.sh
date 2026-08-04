#!/usr/bin/env bash
# Run the deterministic concurrency lane (#357): forced-interleaving tests for
# shared git/remote state (stash-stack races, lock contention, multi-PR batch
# classification). These use real git + sibling worktrees and pre-created lock
# files — no threads, no timing races — so they run in normal CI, not a flaky
# manual lane. Extra args are forwarded to pytest.
set -euo pipefail
exec ./scripts/test.sh tests/concurrency/ -m concurrency "$@"
