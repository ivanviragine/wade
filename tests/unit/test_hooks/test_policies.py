"""Unit tests for the wade write-guard policies (pure predicates)."""

from __future__ import annotations

from pathlib import Path

import pytest
from crossby.hooks.runtime import HookEvent

from wade.hooks.policies import plan_artifact_only, session_complete, worktree_containment

WT = Path("/repo/wt")


def _write(file_path: str | None, tool_name: str = "write") -> HookEvent:
    return HookEvent(tool_name=tool_name, file_path=file_path)


class TestWorktreeContainment:
    def test_inside_absolute_allowed(self) -> None:
        d = worktree_containment(_write("/repo/wt/src/a.py"), worktree_root=WT)
        assert d.action == "allow"

    def test_at_root_allowed(self) -> None:
        d = worktree_containment(_write("/repo/wt/a.py"), worktree_root=WT)
        assert d.action == "allow"

    @pytest.mark.parametrize("path", ["/etc/passwd", "/usr/local/bin/wade", "/repo/other/x.py"])
    def test_outside_denied(self, path: str) -> None:
        d = worktree_containment(_write(path), worktree_root=WT)
        assert d.action == "deny"
        assert str(WT) in d.reason

    def test_relative_resolved_against_cwd(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        d = worktree_containment(_write("nested/a.py"), worktree_root=tmp_path)
        assert d.action == "allow"

    def test_non_write_tool_allowed_even_outside(self) -> None:
        d = worktree_containment(_write("/etc/passwd", tool_name="read"), worktree_root=WT)
        assert d.action == "allow"

    def test_no_file_path_denied(self) -> None:
        # A write whose target can't be read from the payload is denied — the
        # matcher only fires this guard on writes, so we can't verify containment.
        d = worktree_containment(_write(None), worktree_root=WT)
        assert d.action == "deny"
        assert str(WT) in d.reason

    def test_notebook_path_inside_allowed(self) -> None:
        # crossby >= 0.5.0 normalizes NotebookEdit's notebook_path into file_path,
        # so a contained notebook write is allowed (not caught by the deny above).
        ev = HookEvent(tool_name="notebookedit", file_path="/repo/wt/nb.ipynb")
        assert worktree_containment(ev, worktree_root=WT).action == "allow"

    def test_notebook_path_outside_denied(self) -> None:
        ev = HookEvent(tool_name="notebookedit", file_path="/etc/nb.ipynb")
        assert worktree_containment(ev, worktree_root=WT).action == "deny"

    def test_unknown_tool_name_still_checks_path(self) -> None:
        d = worktree_containment(
            HookEvent(tool_name=None, file_path="/etc/passwd"), worktree_root=WT
        )
        assert d.action == "deny"


class TestPlanArtifactOnly:
    @pytest.fixture
    def wt(self, tmp_path: Path, monkeypatch) -> Path:
        # chdir into the worktree so relative artifact paths resolve inside it.
        monkeypatch.chdir(tmp_path)
        return tmp_path

    @pytest.mark.parametrize(
        "path",
        [
            "PLAN.md",
            "PLAN-2.md",
            "prompt.txt",
            ".transcript",
            ".commit-msg",
            "PR-SUMMARY.md",
            ".claude/plans/x.md",
            ".wade/plans/sub/nested.md",
        ],
    )
    def test_allowed_artifacts(self, path: str, wt: Path) -> None:
        assert plan_artifact_only(_write(path), worktree_root=wt).action == "allow"

    @pytest.mark.parametrize("path", ["src/foo.py", "README.md", "pyproject.toml", "setup.py"])
    def test_source_files_denied(self, path: str, wt: Path) -> None:
        d = plan_artifact_only(_write(path), worktree_root=wt)
        assert d.action == "deny"
        assert "plan-session guard" in d.reason

    @pytest.mark.parametrize(
        "path",
        [".claude/plans/../../src/foo.py", ".claude/plans/../../../etc/passwd"],
    )
    def test_traversal_escapes_denied(self, path: str, wt: Path) -> None:
        assert plan_artifact_only(_write(path), worktree_root=wt).action == "deny"

    @pytest.mark.parametrize("path", ["/etc/PLAN.md", "/var/tmp/.claude/plans/x.md"])
    def test_artifact_named_paths_outside_worktree_denied(self, path: str, wt: Path) -> None:
        # Containment runs first — an artifact *basename* outside the worktree is
        # still blocked (plan mode replaces the worktree guard, so it must contain).
        d = plan_artifact_only(_write(path), worktree_root=wt)
        assert d.action == "deny"
        assert "worktree guard" in d.reason

    def test_windows_separators_allowed(self, wt: Path) -> None:
        d = plan_artifact_only(_write(".claude\\plans\\x.md"), worktree_root=wt)
        assert d.action == "allow"

    def test_non_write_tool_allowed(self, wt: Path) -> None:
        d = plan_artifact_only(_write("src/foo.py", tool_name="read"), worktree_root=wt)
        assert d.action == "allow"

    def test_no_file_path_denied(self, wt: Path) -> None:
        # Containment (which plan mode also enforces) denies a pathless write.
        d = plan_artifact_only(_write(None), worktree_root=wt)
        assert d.action == "deny"
        assert "worktree guard" in d.reason


class TestSessionComplete:
    def test_blocks_when_pr_summary_missing(self, tmp_path: Path) -> None:
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path)
        assert d.action == "deny"  # deny == block the stop
        assert "PR-SUMMARY.md" in d.reason

    def test_allows_when_pr_summary_present(self, tmp_path: Path) -> None:
        (tmp_path / "PR-SUMMARY.md").write_text("done")
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path)
        assert d.action == "allow"

    def test_allows_when_nudge_marker_present(self, tmp_path: Path) -> None:
        # Tool-agnostic single-shot: once the marker exists (written by the CLI
        # after a prior block), allow even without Claude's stop_hook_active.
        from wade.hooks.policies import stop_nudge_marker_path

        marker = stop_nudge_marker_path(tmp_path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path)
        assert d.action == "allow"

    def test_symlinked_marker_not_trusted(self, tmp_path: Path) -> None:
        # A planted symlink at the marker path must not count as "already nudged".
        from wade.hooks.policies import stop_nudge_marker_path

        real = tmp_path / "elsewhere"
        real.write_text("")
        marker = stop_nudge_marker_path(tmp_path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.symlink_to(real)
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path)
        assert d.action == "deny"

    def test_symlinked_wade_dir_not_trusted(self, tmp_path: Path) -> None:
        # A symlinked .wade directory must not let an outside file skip the nudge.
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "stop-nudged").write_text("")
        (tmp_path / ".wade").symlink_to(outside)
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path)
        assert d.action == "deny"

    def test_single_shot_allows_when_already_fired(self, tmp_path: Path) -> None:
        # PR-SUMMARY.md still missing, but a Stop hook already fired -> never loop.
        d = session_complete(HookEvent(event="stop", stop_hook_active=True), worktree_root=tmp_path)
        assert d.action == "allow"
