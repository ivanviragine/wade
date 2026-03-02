"""Tests for wade shell-init multi-shell output."""

from __future__ import annotations

from typer.testing import CliRunner

from wade.cli.admin import admin_app

runner = CliRunner()

BASH_OUTPUT = """\
wade() {
  if [[ "${1:-}" == "work" && "${2:-}" == "cd" ]]; then
    local __p
    __p=$(command wade "$@") && cd "$__p"
    return $?
  fi
  command wade "$@"
}"""

FISH_OUTPUT = """\
function wade
  if test (count $argv) -ge 2; and test "$argv[1]" = "work"; and test "$argv[2]" = "cd"
    set -l dir (command wade work cd $argv[3..])
    and builtin cd $dir
  else
    command wade $argv
  end
end"""


def test_bash_output_default() -> None:
    """When SHELL is /bin/bash or unset, output contains bash function syntax."""
    result = runner.invoke(admin_app, ["shell-init"], env={"SHELL": "/bin/bash"})
    assert result.exit_code == 0
    assert "wade()" in result.output
    assert "[[" in result.output
    assert "local" in result.output


def test_zsh_output() -> None:
    """When SHELL is /bin/zsh, output is the same as bash (zsh is bash-compatible for [[ ]])."""
    bash_result = runner.invoke(admin_app, ["shell-init"], env={"SHELL": "/bin/bash"})
    zsh_result = runner.invoke(admin_app, ["shell-init"], env={"SHELL": "/bin/zsh"})
    assert zsh_result.exit_code == 0
    assert zsh_result.output == bash_result.output


def test_fish_output() -> None:
    """When SHELL is /usr/bin/fish, output contains fish function syntax."""
    result = runner.invoke(admin_app, ["shell-init"], env={"SHELL": "/usr/bin/fish"})
    assert result.exit_code == 0
    assert "function wade" in result.output
    assert "end" in result.output
    assert "set -l" in result.output
    assert "test" in result.output


def test_shell_flag_overrides_env() -> None:
    """--shell fish overrides SHELL env var (fish output regardless of SHELL=/bin/bash)."""
    result = runner.invoke(admin_app, ["shell-init", "--shell", "fish"], env={"SHELL": "/bin/bash"})
    assert result.exit_code == 0
    assert "function wade" in result.output
    assert "end" in result.output
    assert "set -l" in result.output


def test_bash_output_unchanged() -> None:
    """Bash output must match current behavior exactly (regression guard)."""
    result = runner.invoke(admin_app, ["shell-init"], env={"SHELL": "/bin/bash"})
    assert result.exit_code == 0
    assert result.output.strip() == BASH_OUTPUT
