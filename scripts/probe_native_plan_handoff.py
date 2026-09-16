#!/usr/bin/env python3
"""Exercise a real native Plan terminal plus WADE import/review/done, without task creation.

Uses normal CLI authentication. Follow the native questions, then exit without
implementing. The temporary workspace and transcripts are retained for inspection.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from crossby.models.ai import PlanSessionRequest

from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.hooks import SessionPhase
from wade.models.permission import PermissionMode
from wade.models.workflow import SessionKind
from wade.services.plan_service import _plan_dir_fallback_env, run_interactive_planning_session
from wade.services.session_composition_service import compose_session


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tool",
        required=True,
        choices=["claude", "cursor", "opencode", "antigravity-cli", "copilot"],
    )
    parser.add_argument("--model")
    parser.add_argument(
        "--worktree", action="store_true", help="Also exercise actual worktree bootstrap and hooks"
    )
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix=f"wade-native-handoff-{args.tool}-")).resolve()
    config = ProjectConfig(ai=AIConfig(review_plan=AICommandConfig(mode="prompt", enabled=True)))
    (root / ".wade.yml").write_text(
        "version: 2\nai:\n  review_plan:\n    enabled: true\n    mode: prompt\n"
    )
    (root / "greeting.py").write_text('print("Hello")\n')
    if args.worktree:
        from wade.git.worktree import create_detached_worktree
        from wade.services.implementation_service import bootstrap_worktree
        from wade.skills.installer import support_skills_for_session

        for command in (
            ["git", "init", "-b", "main"],
            ["git", "add", "greeting.py", ".wade.yml"],
            [
                "git",
                "-c",
                "user.name=WADE probe",
                "-c",
                "user.email=probe@example.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-m",
                "test: seed native handoff probe",
            ],
        ):
            subprocess.run(command, cwd=root, check=True, capture_output=True)
        source = root
        root = create_detached_worktree(repo_root=source, worktree_dir=source / "worktree")
        bootstrap_worktree(
            root,
            config,
            source,
            skills=support_skills_for_session(SessionKind.PLAN),
            plan_mode=True,
            selected_ai_tool=args.tool,
            session_phase=SessionPhase.PLAN,
            session_kind=SessionKind.PLAN,
            sandbox=False,
        )
    else:
        compose_session(root, root, config, kind=SessionKind.PLAN, task_id=None)
    # Child WADE commands must exercise this checkout's installed package.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    plan_dir = str(root / ".wade/plans")
    print(f"Probe workspace: {root}", flush=True)
    print("Complete planning and self-review, then exit the CLI without implementing.", flush=True)
    with _plan_dir_fallback_env(plan_dir, root if args.worktree else None):
        bundle, _ = run_interactive_planning_session(
            args.tool,
            plan_dir,
            request=PlanSessionRequest(
                prompt="Plan handoff probe",
                working_dir=root,
                model=args.model,
                sandbox=False,
            ),
            permission_mode=PermissionMode.DEFAULT,
            config=config,
            issue_context=(
                "# Bounded native planning integration test\n\n"
                "Plan one small task: change greeting.py from Hello to Hi and verify its output. "
                "Do not implement. Use the fixed planning workflow, including readiness, "
                "native user interaction, plan submission, actual self-review, and done. "
                "The parent only checks the handoff; it creates no issues or PRs. "
                "This is a development build: invoke every workflow wade command using "
                f"{shlex.quote(str(Path(sys.executable).parent / 'wade'))} as the executable. "
                "Native login shells may reset PATH to an older installed WADE."
            ),
            session_bundle=str(root / ".wade/session"),
            source_root=str(root),
        )
    print(f"PASS: received {len(bundle.plans)} completed, reviewed plan(s). Artifacts: {plan_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
