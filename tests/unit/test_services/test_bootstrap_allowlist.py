"""Tests for bootstrap_worktree() expanded allowlist propagation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wade.models.config import HooksConfig, PermissionsConfig, ProjectConfig, ProjectSettings
from wade.services.implementation_service import bootstrap_worktree

WADE_ALLOW_PATTERN = "Bash(wade *)"
CURSOR_WADE_ALLOW_PATTERN = "Shell(wade *)"


@pytest.fixture(autouse=True)
def _isolate_cursor_global_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Redirect crossby's global Cursor config to a tmp path so tests don't see ~/.cursor.

    Patches both the legacy alias (older crossby) and the canonical source-of-truth
    in crossby.sync.permissions (newer crossby, where cursor_allowlist._GLOBAL_CONFIG_PATH
    is just an import-time alias and patching it no longer affects CursorPermissionWriter).
    """
    fake_global = tmp_path_factory.mktemp("cursor-home") / "cli-config.json"
    monkeypatch.setattr(
        "crossby.config.cursor_allowlist._GLOBAL_CONFIG_PATH",
        fake_global,
        raising=False,
    )
    monkeypatch.setattr(
        "crossby.sync.permissions._GLOBAL_CURSOR_CONFIG_PATH",
        fake_global,
        raising=False,
    )


class TestBootstrapAllowlistPropagation:
    """Tests that bootstrap_worktree() propagates expanded allowlist patterns."""

    def test_propagates_extra_patterns_from_config(self, tmp_path: Path) -> None:
        """bootstrap_worktree propagates config.permissions.allowed_commands."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Set up existing allowlist at repo root
        repo_claude_dir = repo_root / ".claude"
        repo_claude_dir.mkdir()
        (repo_claude_dir / "settings.json").write_text(
            json.dumps({"permissions": {"allow": [WADE_ALLOW_PATTERN]}}),
            encoding="utf-8",
        )

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(
                allowed_commands=["wade *", "./scripts/check.sh *", "./scripts/fmt.sh *"]
            ),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # Check the worktree settings.json has expanded patterns
        wt_settings = worktree_path / ".claude" / "settings.json"
        assert wt_settings.is_file()
        data = json.loads(wt_settings.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert WADE_ALLOW_PATTERN in allow
        assert "Bash(./scripts/check.sh *)" in allow
        assert "Bash(./scripts/fmt.sh *)" in allow

    def test_always_propagates_even_without_repo_root_settings(self, tmp_path: Path) -> None:
        """bootstrap_worktree always writes allowlist regardless of repo root state."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(allowed_commands=["wade *", "./scripts/check.sh *"]),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # settings.json should always be created, even without repo root config
        wt_settings = worktree_path / ".claude" / "settings.json"
        assert wt_settings.is_file()
        data = json.loads(wt_settings.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]

    def test_guarantees_wade_base_pattern_when_config_omits_it(self, tmp_path: Path) -> None:
        """A project that narrows allowed_commands without 'wade *' still gets
        wade pre-authorized in the worktree.

        Regression: crossby's generic permission writer injects no app-specific
        base pattern, so wade must guarantee its own 'wade *' regardless of the
        user's allowed_commands.
        """
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            # Deliberately omits "wade *".
            permissions=PermissionsConfig(allowed_commands=["./scripts/check.sh *"]),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        wt_settings = worktree_path / ".claude" / "settings.json"
        assert wt_settings.is_file()
        allow = json.loads(wt_settings.read_text(encoding="utf-8"))["permissions"]["allow"]
        assert WADE_ALLOW_PATTERN in allow  # base pattern still guaranteed
        assert "Bash(./scripts/check.sh *)" in allow


class TestBootstrapCursorAllowlistPropagation:
    """Tests that bootstrap_worktree() propagates Cursor allowlist to worktree."""

    def test_propagates_cursor_patterns_from_global(self, tmp_path: Path) -> None:
        """bootstrap_worktree propagates Cursor allowlist when global config exists."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(allowed_commands=["wade *", "./scripts/check.sh *"]),
        )

        # Autouse fixture already redirects _GLOBAL_CONFIG_PATH to a tmp path.
        # is_allowlist_configured is mocked to report the global config as configured
        # (root is None) so propagation flows through.
        with (
            patch(
                "crossby.config.cursor_allowlist.is_allowlist_configured",
                side_effect=lambda root=None, patterns=None: root is None,
            ),
            patch("subprocess.run"),
        ):
            bootstrap_worktree(worktree_path, config, repo_root)

        # Check the worktree .cursor/cli.json has expanded patterns
        wt_cursor_config = worktree_path / ".cursor" / "cli.json"
        assert wt_cursor_config.is_file()
        data = json.loads(wt_cursor_config.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert CURSOR_WADE_ALLOW_PATTERN in allow
        assert "Shell(./scripts/check.sh *)" in allow

    def test_propagates_cursor_patterns_from_project(self, tmp_path: Path) -> None:
        """bootstrap_worktree propagates Cursor allowlist when project config exists."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Set up per-project Cursor config at repo root
        repo_cursor_dir = repo_root / ".cursor"
        repo_cursor_dir.mkdir()
        (repo_cursor_dir / "cli.json").write_text(
            json.dumps({"permissions": {"allow": [CURSOR_WADE_ALLOW_PATTERN]}}),
            encoding="utf-8",
        )

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(allowed_commands=["wade *", "./scripts/fmt.sh *"]),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # Check the worktree .cursor/cli.json has expanded patterns
        wt_cursor_config = worktree_path / ".cursor" / "cli.json"
        assert wt_cursor_config.is_file()
        data = json.loads(wt_cursor_config.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert CURSOR_WADE_ALLOW_PATTERN in allow
        assert "Shell(./scripts/fmt.sh *)" in allow

    def test_skips_cursor_when_not_configured(self, tmp_path: Path) -> None:
        """bootstrap_worktree skips Cursor allowlist if not configured anywhere."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(allowed_commands=["wade *"]),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # No .cursor/cli.json should be created
        wt_cursor_config = worktree_path / ".cursor" / "cli.json"
        assert not wt_cursor_config.is_file()


class TestBootstrapPlanMode:
    """bootstrap_worktree installs ``wade-hook`` guard configs (no copied scripts)."""

    def _config(self) -> ProjectConfig:
        return ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

    def _claude_hook_commands(self, worktree_path: Path) -> list[str]:
        data = json.loads((worktree_path / ".claude" / "settings.json").read_text("utf-8"))
        commands: list[str] = []
        for event_entries in data["hooks"].values():  # PreToolUse, Stop, ...
            for entry in event_entries:
                for hook in entry["hooks"]:
                    assert hook["type"] == "command"
                    commands.append(hook["command"])
        return commands

    def test_plan_mode_installs_wade_hook_configs(self, tmp_path: Path) -> None:
        """plan_mode=True wires each tool's PreToolUse hook to ``wade-hook --guard plan``."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, self._config(), repo_root, plan_mode=True)

        # No standalone guard scripts are copied anymore.
        for tool_dir in [".claude/hooks", ".cursor/hooks", ".copilot/hooks"]:
            assert not (worktree_path / tool_dir / "plan_write_guard.py").exists()
            assert not (worktree_path / tool_dir / "worktree_guard.py").exists()

        # Claude config points PreToolUse at the lean `wade-hook` entry point.
        commands = self._claude_hook_commands(worktree_path)
        assert commands, "no PreToolUse hook installed"
        assert any(
            "wade-hook" in c and "--guard plan" in c and "--tool claude" in c for c in commands
        )

        # Every tool in _hook_writers() gets a config — incl. Codex (the plan
        # guard is finer-grained than any sandbox) and Antigravity CLI, which
        # joined in crossby 0.13 once agy's native tool names landed in the
        # matcher map.
        assert (worktree_path / ".cursor" / "hooks.json").is_file()
        assert (worktree_path / ".github" / "hooks" / "hooks.json").is_file()
        assert (worktree_path / ".codex" / "hooks.json").is_file()
        agents = (worktree_path / ".agents" / "hooks.json").read_text("utf-8")
        assert "wade-hook" in agents
        assert "--guard plan" in agents
        assert "--tool antigravity-cli" in agents

    def test_worktree_mode_installs_wade_hook_configs(self, tmp_path: Path) -> None:
        """Default (worktree) mode wires ``wade-hook --guard worktree`` for non-sandbox tools."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, self._config(), repo_root)

        assert not (worktree_path / ".claude" / "hooks" / "worktree_guard.py").exists()

        commands = self._claude_hook_commands(worktree_path)
        assert any(
            "wade-hook" in c and "--guard worktree" in c and "--tool claude" in c for c in commands
        )
        assert (worktree_path / ".cursor" / "hooks.json").is_file()
        assert (worktree_path / ".github" / "hooks" / "hooks.json").is_file()

    def test_cursor_write_guard_is_fail_closed(self, tmp_path: Path) -> None:
        """Cursor's write guard sets failClosed (fail-open by default); Stop stays fail-open."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, self._config(), repo_root)

        # crossby 0.13 writes Cursor's documented shape: {"version": 1, "hooks": {...}}.
        cursor = json.loads((worktree_path / ".cursor" / "hooks.json").read_text("utf-8"))
        hooks = cursor["hooks"]
        pre = hooks["preToolUse"]
        assert pre and all(entry.get("failClosed") is True for entry in pre)
        # The Stop hook must NOT be fail-closed (it must never trap the agent).
        for entry in hooks.get("stop", []):
            assert entry.get("failClosed") is not True

    def test_worktree_guard_narrowed_to_shell_for_sandboxed_codex(self, tmp_path: Path) -> None:
        """Codex's sandbox covers tool-call writes but not shell redirects to /tmp.

        ``--sandbox workspace-write`` permits ``/tmp`` and ``$TMPDIR``, so a shell
        redirect is sandbox-legal yet lands outside the worktree. The guard is
        therefore narrowed to the shell matcher rather than skipped entirely.
        """
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, self._config(), repo_root)  # worktree mode

        codex = json.loads((worktree_path / ".codex" / "hooks.json").read_text("utf-8"))
        pre_entries = codex["hooks"]["PreToolUse"]
        assert pre_entries, "Codex must still receive a shell-scoped worktree guard"
        # Scoped to the shell token only — the file-write half is the sandbox's job.
        assert all(entry.get("matcher") == "Bash" for entry in pre_entries)
        assert any(
            "--guard worktree" in hook["command"]
            for entry in pre_entries
            for hook in entry["hooks"]
        )
        assert "session-complete" in (worktree_path / ".codex" / "hooks.json").read_text("utf-8")

    def test_codex_hooks_feature_flag_enabled(self, tmp_path: Path) -> None:
        """crossby's Codex writer enables [features].codex_hooks so hooks load."""
        import tomllib

        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, self._config(), repo_root)

        config = worktree_path / ".codex" / "config.toml"
        assert config.is_file(), "Codex hooks are inert without .codex/config.toml"
        parsed = tomllib.loads(config.read_text("utf-8"))
        assert parsed["features"]["codex_hooks"] is True

    def test_stop_hook_installed_for_implement_not_plan(self, tmp_path: Path) -> None:
        """Implement/review sessions install a Stop completion hook; plan sessions do not."""
        # Implement (worktree) mode → Stop hook present for Stop-capable Claude.
        wt = tmp_path / "impl"
        wt.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run"):
            bootstrap_worktree(wt, self._config(), repo)
        commands = self._claude_hook_commands(wt)
        assert any(
            "wade-hook stop --guard session-complete" in c and "--tool claude" in c
            for c in commands
        )

        # Plan mode → no Stop hook.
        wt2 = tmp_path / "plan"
        wt2.mkdir()
        with patch("subprocess.run"):
            bootstrap_worktree(wt2, self._config(), repo, plan_mode=True)
        commands2 = self._claude_hook_commands(wt2)
        assert not any("session-complete" in c for c in commands2)

    def test_stop_hook_installed_for_antigravity_cli(self, tmp_path: Path) -> None:
        """agy supports_stop_hook=True, so its Stop hook lands in .agents/hooks.json."""
        wt = tmp_path / "impl"
        wt.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("subprocess.run"):
            bootstrap_worktree(wt, self._config(), repo)
        agy_config = wt / ".agents" / "hooks.json"
        assert agy_config.is_file(), "agy Stop hook config should be written"
        body = agy_config.read_text("utf-8")
        # agy's Stop handlers sit directly under a "Stop" key (no matcher wrapper).
        assert "Stop" in body
        assert "wade-hook stop --guard session-complete" in body
        assert "--tool antigravity-cli" in body


class TestBootstrapPointerInjection:
    """Tests that bootstrap_worktree() injects the AGENTS.md pointer into worktrees."""

    def test_pointer_written_to_worktree(self, tmp_path: Path) -> None:
        """bootstrap_worktree writes the AGENTS.md pointer to the worktree."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # AGENTS.md should be created in the worktree with the pointer block
        agents_md = worktree_path / "AGENTS.md"
        assert agents_md.is_file(), "AGENTS.md pointer should be written to worktree"
        content = agents_md.read_text()
        assert "<!-- wade:pointer:start -->" in content

    def test_pointer_not_written_to_main(self, tmp_path: Path) -> None:
        """bootstrap_worktree does not touch repo_root AGENTS.md."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # repo_root should not have AGENTS.md
        assert not (repo_root / "AGENTS.md").is_file()

    def test_pointer_follows_existing_agents_content(self, tmp_path: Path) -> None:
        """bootstrap_worktree appends pointer after existing AGENTS.md content."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Pre-populate AGENTS.md with project content
        (worktree_path / "AGENTS.md").write_text("# Project Guide\n\nExisting content.\n")

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        content = (worktree_path / "AGENTS.md").read_text()
        assert "# Project Guide" in content
        assert "<!-- wade:pointer:start -->" in content
