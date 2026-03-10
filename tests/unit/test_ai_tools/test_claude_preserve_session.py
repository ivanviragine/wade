"""Tests for ClaudeAdapter.preserve_session_data() and session_data_dirs()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.ai_tools.claude import ClaudeAdapter, _encode_claude_path


class TestEncodePath:
    """Unit tests for _encode_claude_path."""

    def test_plain_path(self) -> None:
        assert _encode_claude_path(Path("/Users/foo/bar")) == "-Users-foo-bar"

    def test_hidden_directory(self) -> None:
        """Dots in hidden directories like .worktrees are replaced with dashes."""
        assert (
            _encode_claude_path(Path("/Users/foo/.worktrees/proj/feat-1"))
            == "-Users-foo--worktrees-proj-feat-1"
        )

    def test_multiple_hidden_segments(self) -> None:
        assert (
            _encode_claude_path(Path("/Users/foo/.codex/worktrees/c780/.worktrees/proj"))
            == "-Users-foo--codex-worktrees-c780--worktrees-proj"
        )


class TestClaudeSessionDataDirs:
    def test_returns_dot_claude(self) -> None:
        adapter = ClaudeAdapter()
        assert adapter.session_data_dirs() == [".claude"]


class TestClaudePreserveSessionData:
    def test_no_source_dir_returns_true(self, tmp_path: Path) -> None:
        """When no worktree session dir exists, returns True (nothing to copy)."""
        adapter = ClaudeAdapter()
        fake_home = tmp_path / "home"
        (fake_home / ".claude" / "projects").mkdir(parents=True)

        wt_path = tmp_path / "worktrees" / "feat-1-thing"
        main_path = tmp_path / "repo"

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True

    def test_copies_files_to_main_session_dir(self, tmp_path: Path) -> None:
        """Session files from worktree are copied to main checkout's project dir."""
        adapter = ClaudeAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / "worktrees" / "feat-1"
        main_path = tmp_path / "repo"

        # Set up source session dir
        wt_encoded = str(wt_path).replace("/", "-").replace(".", "-")
        wt_session_dir = fake_home / ".claude" / "projects" / wt_encoded
        wt_session_dir.mkdir(parents=True)
        (wt_session_dir / "session-abc.jsonl").write_text('{"type":"message"}\n')
        (wt_session_dir / "settings.json").write_text('{"theme":"dark"}\n')

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        main_encoded = str(main_path).replace("/", "-").replace(".", "-")
        main_session_dir = fake_home / ".claude" / "projects" / main_encoded
        assert (main_session_dir / "session-abc.jsonl").exists()
        assert (main_session_dir / "settings.json").exists()

    def test_does_not_overwrite_existing_files(self, tmp_path: Path) -> None:
        """Files already in the main session dir are never overwritten."""
        adapter = ClaudeAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / "worktrees" / "feat-2"
        main_path = tmp_path / "repo"

        wt_encoded = str(wt_path).replace("/", "-").replace(".", "-")
        wt_session_dir = fake_home / ".claude" / "projects" / wt_encoded
        wt_session_dir.mkdir(parents=True)
        (wt_session_dir / "settings.json").write_text("from-worktree\n")

        main_encoded = str(main_path).replace("/", "-").replace(".", "-")
        main_session_dir = fake_home / ".claude" / "projects" / main_encoded
        main_session_dir.mkdir(parents=True)
        (main_session_dir / "settings.json").write_text("original-main\n")

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        assert (main_session_dir / "settings.json").read_text() == "original-main\n"

    def test_copies_subdirectories(self, tmp_path: Path) -> None:
        """Subdirectories (e.g. memory dirs) are copied recursively."""
        adapter = ClaudeAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / "worktrees" / "feat-3"
        main_path = tmp_path / "repo"

        wt_encoded = str(wt_path).replace("/", "-").replace(".", "-")
        wt_session_dir = fake_home / ".claude" / "projects" / wt_encoded
        subdir = wt_session_dir / "memory"
        subdir.mkdir(parents=True)
        (subdir / "notes.md").write_text("# Notes\n")

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        main_encoded = str(main_path).replace("/", "-").replace(".", "-")
        main_session_dir = fake_home / ".claude" / "projects" / main_encoded
        assert (main_session_dir / "memory" / "notes.md").exists()

    def test_creates_main_session_dir_if_missing(self, tmp_path: Path) -> None:
        """Main session directory is created if it doesn't already exist."""
        adapter = ClaudeAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / "worktrees" / "feat-4"
        main_path = tmp_path / "repo"

        wt_encoded = str(wt_path).replace("/", "-").replace(".", "-")
        wt_session_dir = fake_home / ".claude" / "projects" / wt_encoded
        wt_session_dir.mkdir(parents=True)
        (wt_session_dir / "session.jsonl").write_text("{}\n")

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        main_encoded = str(main_path).replace("/", "-").replace(".", "-")
        main_session_dir = fake_home / ".claude" / "projects" / main_encoded
        assert main_session_dir.is_dir()

    def test_copies_with_dotfile_path_segments(self, tmp_path: Path) -> None:
        """Paths containing hidden dirs like .worktrees are encoded correctly."""
        adapter = ClaudeAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / ".worktrees" / "proj" / "feat-5"
        main_path = tmp_path / "repo"

        wt_encoded = str(wt_path).replace("/", "-").replace(".", "-")
        wt_session_dir = fake_home / ".claude" / "projects" / wt_encoded
        wt_session_dir.mkdir(parents=True)
        (wt_session_dir / "session.jsonl").write_text("{}\n")

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        main_encoded = str(main_path).replace("/", "-").replace(".", "-")
        main_session_dir = fake_home / ".claude" / "projects" / main_encoded
        assert (main_session_dir / "session.jsonl").exists()
