"""Tests for CursorAdapter.preserve_session_data() and session_data_dirs()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.ai_tools.cursor import CursorAdapter


class TestCursorSessionDataDirs:
    def test_returns_dot_cursor(self) -> None:
        adapter = CursorAdapter()
        assert adapter.session_data_dirs() == [".cursor"]


class TestCursorPreserveSessionData:
    def test_no_source_dir_returns_true(self, tmp_path: Path) -> None:
        """When no worktree session dir exists, returns True (nothing to copy)."""
        adapter = CursorAdapter()
        fake_home = tmp_path / "home"
        (fake_home / ".cursor" / "projects").mkdir(parents=True)

        wt_path = tmp_path / "worktrees" / "feat-1-thing"
        main_path = tmp_path / "repo"

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True

    def test_path_encoding_strips_leading_slash(self, tmp_path: Path) -> None:
        """Cursor encodes paths without a leading dash (strips leading '/')."""
        adapter = CursorAdapter()
        fake_home = tmp_path / "home"

        wt_path = Path("/Users/foo/worktrees/feat-1")
        main_path = Path("/Users/foo/repo")

        # Expected encoded names (no leading dash)
        wt_encoded = "Users-foo-worktrees-feat-1"
        main_encoded = "Users-foo-repo"

        wt_session_dir = fake_home / ".cursor" / "projects" / wt_encoded
        wt_session_dir.mkdir(parents=True)
        (wt_session_dir / "session.json").write_text('{"id":"abc"}\n')

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        main_session_dir = fake_home / ".cursor" / "projects" / main_encoded
        assert (main_session_dir / "session.json").exists()

    def test_copies_files_to_main_session_dir(self, tmp_path: Path) -> None:
        """Session files from worktree are copied to main checkout's project dir."""
        adapter = CursorAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / "worktrees" / "feat-2"
        main_path = tmp_path / "repo"

        wt_encoded = str(wt_path).lstrip("/").replace("/", "-")
        wt_session_dir = fake_home / ".cursor" / "projects" / wt_encoded
        wt_session_dir.mkdir(parents=True)
        (wt_session_dir / "chat-abc.json").write_text('{"messages":[]}\n')
        (wt_session_dir / ".workspace-trusted").write_text("{}\n")

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        main_encoded = str(main_path).lstrip("/").replace("/", "-")
        main_session_dir = fake_home / ".cursor" / "projects" / main_encoded
        assert (main_session_dir / "chat-abc.json").exists()
        assert (main_session_dir / ".workspace-trusted").exists()

    def test_does_not_overwrite_existing_files(self, tmp_path: Path) -> None:
        """Files already in the main session dir are never overwritten."""
        adapter = CursorAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / "worktrees" / "feat-3"
        main_path = tmp_path / "repo"

        wt_encoded = str(wt_path).lstrip("/").replace("/", "-")
        wt_session_dir = fake_home / ".cursor" / "projects" / wt_encoded
        wt_session_dir.mkdir(parents=True)
        (wt_session_dir / ".workspace-trusted").write_text("from-worktree\n")

        main_encoded = str(main_path).lstrip("/").replace("/", "-")
        main_session_dir = fake_home / ".cursor" / "projects" / main_encoded
        main_session_dir.mkdir(parents=True)
        (main_session_dir / ".workspace-trusted").write_text("original-main\n")

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        assert (main_session_dir / ".workspace-trusted").read_text() == "original-main\n"

    def test_copies_subdirectories(self, tmp_path: Path) -> None:
        """Subdirectories are copied recursively."""
        adapter = CursorAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / "worktrees" / "feat-4"
        main_path = tmp_path / "repo"

        wt_encoded = str(wt_path).lstrip("/").replace("/", "-")
        wt_session_dir = fake_home / ".cursor" / "projects" / wt_encoded
        subdir = wt_session_dir / "agent-transcripts"
        subdir.mkdir(parents=True)
        (subdir / "transcript-1.json").write_text("[]\n")

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        main_encoded = str(main_path).lstrip("/").replace("/", "-")
        main_session_dir = fake_home / ".cursor" / "projects" / main_encoded
        assert (main_session_dir / "agent-transcripts" / "transcript-1.json").exists()

    def test_creates_main_session_dir_if_missing(self, tmp_path: Path) -> None:
        """Main session directory is created if it doesn't already exist."""
        adapter = CursorAdapter()
        fake_home = tmp_path / "home"

        wt_path = tmp_path / "worktrees" / "feat-5"
        main_path = tmp_path / "repo"

        wt_encoded = str(wt_path).lstrip("/").replace("/", "-")
        wt_session_dir = fake_home / ".cursor" / "projects" / wt_encoded
        wt_session_dir.mkdir(parents=True)
        (wt_session_dir / "chat.json").write_text("{}\n")

        with patch.object(Path, "home", return_value=fake_home):
            result = adapter.preserve_session_data(wt_path, main_path)

        assert result is True
        main_encoded = str(main_path).lstrip("/").replace("/", "-")
        main_session_dir = fake_home / ".cursor" / "projects" / main_encoded
        assert main_session_dir.is_dir()
