"""Write-guard policies for AI sessions.

The decision logic for wade's write guards lives in :mod:`wade.hooks.policies`
as pure predicates over a normalized ``crossby.hooks.runtime.HookEvent``. They
are invoked out-of-process by the ``wade hook`` CLI entry point (see
:mod:`wade.cli.hook`), which AI tools call from their PreToolUse hooks — the
per-tool stdin parsing / decision emitting is handled by ``crossby.hooks.runtime``.
"""

from __future__ import annotations

from wade.hooks.policies import plan_artifact_only, worktree_containment

__all__ = ["plan_artifact_only", "worktree_containment"]
