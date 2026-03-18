"""Tests for probe_cli_args and probe_codex web fallback in probe_models.py."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess:  # type: ignore[type-arg]
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# probe_cli_args — basic flag detection
# ---------------------------------------------------------------------------


class TestProbeCliArgs:
    def test_flag_found_in_stdout(self) -> None:
        from probe_models import probe_cli_args

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch(
                "subprocess.run",
                return_value=_completed(stdout="--print  Run in headless mode\n--resume <id>"),
            ),
        ):
            result = probe_cli_args("claude")

        assert result["headless"] is True
        assert result["resume"] is True

    def test_flag_found_in_stderr(self) -> None:
        from probe_models import probe_cli_args

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch(
                "subprocess.run",
                return_value=_completed(stderr="--dangerously-skip-permissions skip checks"),
            ),
        ):
            result = probe_cli_args("claude")

        assert result["yolo"] is True

    def test_missing_flag_returns_false(self) -> None:
        from probe_models import probe_cli_args

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch(
                "subprocess.run",
                return_value=_completed(stdout="--model  Set the model\n--print  Headless mode"),
            ),
        ):
            result = probe_cli_args("claude")

        # --resume and --permission-mode and --dangerously-skip-permissions are absent
        assert result["resume"] is False
        assert result["permission_mode"] is False
        assert result["yolo"] is False

    def test_binary_not_installed_returns_empty(self) -> None:
        from probe_models import probe_cli_args

        with patch("shutil.which", return_value=None):
            result = probe_cli_args("claude")

        assert result == {}

    def test_unknown_tool_returns_empty(self) -> None:
        from probe_models import probe_cli_args

        result = probe_cli_args("nonexistent-tool")
        assert result == {}

    def test_fallback_to_dash_h_when_help_returns_empty(self) -> None:
        """When --help returns empty output, probe falls back to -h."""
        from probe_models import probe_cli_args

        call_count = 0

        def fake_run(cmd: list[str], **_: object) -> CompletedProcess:  # type: ignore[type-arg]
            nonlocal call_count
            call_count += 1
            if "--help" in cmd:
                return _completed(stdout="", stderr="")
            # -h fallback
            return _completed(stdout="--print  headless\n--resume  resume session")

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = probe_cli_args("claude")

        assert result["headless"] is True
        assert result["resume"] is True
        assert call_count == 2  # Both --help and -h were tried

    def test_subprocess_exception_returns_empty(self) -> None:
        from probe_models import probe_cli_args

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("subprocess.run", side_effect=OSError("binary not found")),
        ):
            result = probe_cli_args("claude")

        assert result == {}

    def test_codex_flags_detected(self) -> None:
        from probe_models import probe_cli_args

        codex_help = (
            "Usage: codex [options] <prompt>\n"
            "  exec           Run in headless mode\n"
            "  --approval-mode  Set approval mode (full-auto)\n"
            "  -c model_reasoning_effort=<level>  Set reasoning effort\n"
            "  --profile      Use a profile\n"
            "  --image        Attach an image\n"
            "  --ask-for-approval  Ask before running\n"
        )
        with (
            patch("shutil.which", return_value="/usr/bin/codex"),
            patch("subprocess.run", return_value=_completed(stdout=codex_help)),
        ):
            result = probe_cli_args("codex")

        assert result["yolo"] is True
        assert result["headless"] is True
        assert result["model_reasoning_effort"] is True
        assert result["profile"] is True
        assert result["image"] is True
        assert result["ask_for_approval"] is True

    def test_opencode_flags_detected(self) -> None:
        from probe_models import probe_cli_args

        opencode_help = (
            "Usage: opencode [command]\n"
            "  run    Execute in headless mode\n"
            "  --model  Select model\n"
            "  -s     Resume session\n"
            "  --variant  Set effort variant\n"
        )
        with (
            patch("shutil.which", return_value="/usr/bin/opencode"),
            patch("subprocess.run", return_value=_completed(stdout=opencode_help)),
        ):
            result = probe_cli_args("opencode")

        assert result["headless"] is True
        assert result["model"] is True
        assert result["resume"] is True
        assert result["effort"] is True


# ---------------------------------------------------------------------------
# probe_codex — web scrape fallback
# ---------------------------------------------------------------------------


class TestProbeCodexFallback:
    def test_returns_from_cache_when_available(self, tmp_path: Path) -> None:
        from probe_models import probe_codex

        cache_data = {
            "models": [
                {"slug": "gpt-4o", "visibility": "list"},
                {"slug": "gpt-4o-mini", "visibility": "list"},
                {"slug": "gpt-secret", "visibility": "hidden"},
            ]
        }
        home = tmp_path / "home"
        codex_dir = home / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "models_cache.json").write_text(json.dumps(cache_data))

        with patch("probe_models.Path.home", return_value=home):
            result = probe_codex()

        assert result == {"gpt-4o", "gpt-4o-mini"}

    def test_falls_back_to_scrape_when_cache_missing(self, tmp_path: Path) -> None:
        from probe_models import probe_codex

        # Point home to a directory with no .codex/models_cache.json
        with (
            patch("probe_models.Path.home", return_value=tmp_path),
            patch(
                "probe_models._scrape_models", return_value={"gpt-5.0-turbo", "gpt-4.5"}
            ) as mock_scrape,
        ):
            result = probe_codex()

        mock_scrape.assert_called_once_with("codex")
        assert result == {"gpt-5.0-turbo", "gpt-4.5"}

    def test_falls_back_to_scrape_when_cache_empty_models(self, tmp_path: Path) -> None:
        from probe_models import probe_codex

        cache_data: dict[str, list[object]] = {"models": []}
        home = tmp_path / "home"
        codex_dir = home / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "models_cache.json").write_text(json.dumps(cache_data))

        with (
            patch("probe_models.Path.home", return_value=home),
            patch("probe_models._scrape_models", return_value={"gpt-4.5"}) as mock_scrape,
        ):
            result = probe_codex()

        mock_scrape.assert_called_once_with("codex")
        assert result == {"gpt-4.5"}

    def test_falls_back_to_scrape_when_cache_json_invalid(self, tmp_path: Path) -> None:
        from probe_models import probe_codex

        home = tmp_path / "home"
        codex_dir = home / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "models_cache.json").write_text("not valid json{{{")

        with (
            patch("probe_models.Path.home", return_value=home),
            patch("probe_models._scrape_models", return_value={"gpt-4.5"}) as mock_scrape,
        ):
            result = probe_codex()

        mock_scrape.assert_called_once_with("codex")
        assert result == {"gpt-4.5"}

    def test_cache_with_no_list_visibility_falls_back(self, tmp_path: Path) -> None:
        from probe_models import probe_codex

        # Cache exists but all models have non-list visibility
        cache_data = {
            "models": [
                {"slug": "gpt-internal", "visibility": "hidden"},
                {"slug": "gpt-private", "visibility": "private"},
            ]
        }
        home = tmp_path / "home"
        codex_dir = home / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "models_cache.json").write_text(json.dumps(cache_data))

        with (
            patch("probe_models.Path.home", return_value=home),
            patch("probe_models._scrape_models", return_value={"gpt-4.5"}) as mock_scrape,
        ):
            result = probe_codex()

        mock_scrape.assert_called_once_with("codex")
        assert result == {"gpt-4.5"}


# ---------------------------------------------------------------------------
# _EXPECTED_FLAGS and _DOCS_URLS / _SCRAPE_PATTERNS constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_expected_flags_covers_all_required_tools(self) -> None:
        from probe_models import _EXPECTED_FLAGS

        required = {"codex", "gemini", "claude", "copilot", "cursor", "opencode"}
        assert required.issubset(_EXPECTED_FLAGS.keys())

    def test_codex_in_docs_urls(self) -> None:
        from probe_models import _DOCS_URLS

        assert "codex" in _DOCS_URLS
        assert "developers.openai.com" in _DOCS_URLS["codex"]

    def test_codex_in_scrape_patterns(self) -> None:
        import re

        from probe_models import _SCRAPE_PATTERNS

        assert "codex" in _SCRAPE_PATTERNS
        pattern = _SCRAPE_PATTERNS["codex"]
        assert re.search(pattern, "gpt-4.5-turbo")
        assert re.search(pattern, "gpt-4o")
        assert not re.search(pattern, "claude-opus-4")
