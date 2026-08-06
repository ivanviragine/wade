"""Unit tests for the wade write-guard policies (pure predicates)."""

from __future__ import annotations

from pathlib import Path

import pytest
from crossby.hooks.runtime import HookEvent

from wade.hooks.policies import (
    plan_artifact_only,
    session_complete,
    shell_containment,
    worktree_containment,
)

WT = Path("/repo/wt")


def _write(file_path: str | None, tool_name: str = "write") -> HookEvent:
    return HookEvent(tool_name=tool_name, file_path=file_path)


def _shell(command: str, cwd: str | None = str(WT)) -> HookEvent:
    return HookEvent(tool_name="bash", command=command, cwd=cwd)


class TestFailClosedWriteDetection:
    """crossby 0.13 inverted ``is_write`` to a denylist — unknown names are writes.

    Regression cover for the fail-*open* allowlist that let Codex ``apply_patch``
    and agy ``write_to_file`` past every containment guard.
    """

    @pytest.mark.parametrize(
        "tool_name",
        ["apply_patch", "write_to_file", "replace_file_content", "some_future_tool"],
    )
    def test_unrecognized_write_names_denied_outside_worktree(self, tool_name: str) -> None:
        d = worktree_containment(_write("/etc/passwd", tool_name), worktree_root=WT)
        assert d.action == "deny"

    @pytest.mark.parametrize(
        "tool_name",
        ["apply_patch", "write_to_file", "replace_file_content", "some_future_tool"],
    )
    def test_unrecognized_write_names_denied_in_plan_mode(self, tool_name: str) -> None:
        d = plan_artifact_only(_write("/repo/wt/src/app.py", tool_name), worktree_root=WT)
        assert d.action == "deny"

    @pytest.mark.parametrize("tool_name", ["read", "grep", "view_file", "glob", "list_dir"])
    def test_known_read_names_allowed(self, tool_name: str) -> None:
        d = worktree_containment(_write("/etc/passwd", tool_name), worktree_root=WT)
        assert d.action == "allow"


class TestShellContainment:
    """The shell channel — crossby reports ``is_write=False`` for shell tool names."""

    @pytest.mark.parametrize(
        "command",
        [
            "ls -la",
            "./scripts/test.sh",
            "rg foo src/",
            "git status",
            "/usr/bin/env python foo.py",  # absolute *executable* is exempt
            "printf x > out.txt",
            "printf x >> notes.md",
            "cd src && ls",
            "cat ./a/b.txt",
            "echo hi 2>&1",  # fd duplication names no path
            "cat < in.txt",
        ],
    )
    def test_inside_worktree_allowed(self, command: str) -> None:
        assert shell_containment(_shell(command), worktree_root=WT).action == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "printf 'x' > /tmp/out.txt",  # spaced redirect
            "printf 'x' >/tmp/out.txt",  # glued redirect
            "printf x >>../main/file",  # relative traversal, glued append
            'printf x > "/tmp/quoted"',  # quoting does not hide the target
            "printf x > /tmp/esc\\ aped",  # escaped space
            "cd /etc && rm x",  # cd outside rebases later writes
            "cd ..",  # bare .. carries no slash
            "cd ../../elsewhere",
            "cp a /tmp/b",
            "git checkout -- ../main-repo/x",
            "echo a | tee /etc/p",
            "sed -i '' s/a/b/ ../main/file",  # in-place edit outside (impl mode)
            "true; cp a /tmp/b",  # ;-chained segment
            "true && cp a /tmp/b",  # &&-chained segment
            "mkdir /tmp/outside-dir",  # mkdir creates outside
            "git clone https://example.com/r.git /tmp/outside",  # positional write
            "git init /tmp/outside",
            "git worktree add /tmp/outside main",  # literal worktree escape
            "git -C /tmp/outside clean -fd",  # spaced -C + git write subcommand
            "git -C /tmp/outside checkout -- file",
        ],
    )
    def test_outside_worktree_denied(self, command: str) -> None:
        assert shell_containment(_shell(command), worktree_root=WT).action == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "cat ../crossby/x.py",  # reading a sibling repo never mutates state
            "grep -r foo ../crossby",
            "rg foo ../crossby",
            "ls ../crossby",
            "head ../sib/f",
            "git -C ../crossby log",  # read subcommand through an outside -C dir
            "diff ../a ../b",
            "cat ~/secrets",  # ~ expands outside, but a read is fine
            "cat < /etc/passwd",  # input redirect only reads its target
        ],
    )
    def test_reads_outside_worktree_allowed(self, command: str) -> None:
        """Reads outside the worktree are allowed — only writes are contained."""
        assert shell_containment(_shell(command), worktree_root=WT).action == "allow"
        # ...and equally in plan mode (which still blocks every non-artifact write).
        plan = shell_containment(_shell(command), worktree_root=WT, plan_mode=True)
        assert plan.action == "allow"

    @pytest.mark.parametrize("command", ['echo "unterminated', "echo 'unbalanced"])
    def test_unparseable_fails_closed(self, command: str) -> None:
        """An untokenizable command may hide anything — deny rather than guess."""
        assert shell_containment(_shell(command), worktree_root=WT).action == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "command -v gh >/dev/null 2>&1",
            "git rev-parse --show-toplevel 2>/dev/null",
            "./scripts/test.sh > /dev/null",
            "make build &>/dev/null",
            "cat /dev/null",
        ],
    )
    def test_device_redirects_allowed(self, command: str) -> None:
        """``>/dev/null 2>&1`` is the commonest shell idiom there is, not an escape."""
        assert shell_containment(_shell(command), worktree_root=WT).action == "allow"

    @pytest.mark.parametrize("command", ["echo hi 2>&1", "echo hi >&2", "exec 3>&-"])
    def test_fd_duplication_names_no_file(self, command: str) -> None:
        assert shell_containment(_shell(command), worktree_root=WT).action == "allow"

    @pytest.mark.parametrize("command", ["printf x >&/tmp/pwn", "printf x >>&/tmp/pwn"])
    def test_glued_ampersand_redirect_to_file_denied(self, command: str) -> None:
        """bash's ``>&word`` with a filename is a real write, not an fd dup."""
        assert shell_containment(_shell(command), worktree_root=WT).action == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "sort --output=/tmp/pwn file",
            "tar --file=/tmp/pwn.tar -c .",
            "curl -o/tmp/pwn https://example.com",
            "dd of=/tmp/pwn if=README.md",
            "git -C/tmp/other init",
        ],
    )
    def test_paths_glued_to_flags_denied(self, command: str) -> None:
        """A path attached to a flag or operand is still a path.

        These are the *glued* forms, kept contained in every mode because a
        tokenizer cannot tell a glued read flag from a glued write flag.

        The *spaced* counterparts are accepted residual write-escape risks (not
        asserted here, documented in the ``shell_containment`` docstring): spaced
        output flags on non-write commands (``curl -o /outside``,
        ``sort --output /outside``), directory-context flags on non-git
        extractors/builders (``tar -C /outside -xf a.tar``, ``unzip -d /outside``,
        ``make -C /outside``), and conditional-write ``find`` (``find ../outside
        -delete`` / ``-exec rm {} +``). Each is skipped rather than fixed because a
        blanket rule would re-block the sibling-repo reads this relaxation exists to
        allow (``ls -o ../dir``, ``find ../crossby -name '*.py'``).
        """
        assert shell_containment(_shell(command), worktree_root=WT).action == "deny"

    @pytest.mark.parametrize(
        "command",
        ["rg --glob=*.py foo", "sort --output=out.txt file", "tar --file=x.tar -c ."],
    )
    def test_relative_glued_flag_values_allowed(self, command: str) -> None:
        assert shell_containment(_shell(command), worktree_root=WT).action == "allow"

    @pytest.mark.parametrize(
        "command",
        ["cd && printf x > pwn.txt", "cd - && printf x > pwn.txt", "cd ~-", "cd"],
    )
    def test_bare_cd_denied(self, command: str) -> None:
        """A bare ``cd`` lands in $HOME, rebasing every later relative write."""
        assert shell_containment(_shell(command), worktree_root=WT).action == "deny"

    def test_glued_separator_starts_a_new_segment(self) -> None:
        """``shlex`` leaves ``;``/``|`` glued to a word; the executable must still parse."""
        assert shell_containment(_shell("echo hi; /bin/ls"), worktree_root=WT).action == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "tee 'a|b' /tmp/pwn",  # quoted '|' must not fake a segment break
            "tee 'a;b' /tmp/pwn",
            "grep 'x&y' f && cp a /tmp/b",  # real separator after a quoted one
        ],
    )
    def test_quoted_separators_do_not_hide_the_next_operand(self, command: str) -> None:
        """A separator *inside* a quoted word is data, not a new command segment.

        Re-splitting tokens on ``|;&`` after ``shlex.split`` stripped the quotes
        turned ``a|b`` into ``a``/``;``/``b``; the phantom ``;`` promoted the
        following path to an *executable*, which is exempt from containment.
        """
        assert shell_containment(_shell(command), worktree_root=WT).action == "deny"

    def test_quoted_separator_operand_checked_in_plan_mode(self) -> None:
        d = shell_containment(_shell("tee 'a|b' src/app.py"), worktree_root=WT, plan_mode=True)
        assert d.action == "deny"

    def test_empty_command_allowed(self) -> None:
        assert shell_containment(_shell(""), worktree_root=WT).action == "allow"

    def test_cwd_outside_root_does_not_widen_the_root(self) -> None:
        """A payload-supplied cwd cannot be used to escape containment."""
        d = shell_containment(_shell("printf x > out.txt", cwd="/etc"), worktree_root=WT)
        assert d.action == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "printf x > src/app.py",  # redirect to a non-artifact
            "printf x >> src/app.py",
            "sed -i.bak s/a/b/ src/app.py",  # in-place edit
            "sed --in-place s/a/b/ src/app.py",
            "echo x | tee src/app.py",  # tee to a non-artifact
            "echo x | tee app.py",  # bare filename — no "/" to look path-like
            "echo x|tee src/app.py",  # glued pipe still starts a new segment
            "cp PLAN.md src/app.py",  # non-redirect write commands
            "mv PLAN.md src/app.py",
            "touch src/app.py",
            "git checkout main -- src/app.py",
            "rm src/app.py",  # deletion is a write
            "rm -rf src/",
            "rmdir src",
            "unlink src/app.py",
            "printf x > /tmp/o",  # outside wins regardless
        ],
    )
    def test_plan_mode_denies_non_artifact_writes(self, command: str) -> None:
        assert shell_containment(_shell(command), worktree_root=WT, plan_mode=True).action == "deny"

    @pytest.mark.parametrize(
        "command",
        [
            "printf x > PLAN.md",
            "printf x > PLAN-2-thing.md",
            "echo x >> PR-SUMMARY.md",
            "printf x > .claude/plans/a.md",
            "echo x | tee PLAN.md",
            "cp PLAN.md PLAN-2.md",  # write command, artifact operands
            "rm PLAN.md",  # deleting an artifact is allowed
            "ls -la",
            "cat src/app.py",  # reading source is fine in plan mode
            "cat < src/app.py",  # input redirect is a read, not a write
        ],
    )
    def test_plan_mode_allows_artifacts_and_reads(self, command: str) -> None:
        got = shell_containment(_shell(command), worktree_root=WT, plan_mode=True).action
        assert got == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "grep -i pattern src/app.py",
            "rg -i foo",
            "ls -i src",
            "git commit -i",
            "sort -i notes.txt",
        ],
    )
    def test_plan_mode_allows_read_only_dash_i(self, command: str) -> None:
        """``-i`` means in-place only for a handful of commands, not universally.

        Matching a bare ``-i`` on *any* command denied `grep -i`, `rg -i` and
        `ls -i` in plan mode — with a message about "in-place editing" that was
        wrong for all three.
        """
        got = shell_containment(_shell(command), worktree_root=WT, plan_mode=True).action
        assert got == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "sed -i s/a/b/ src/app.py",
            "perl -i -pe s/a/b/ src/app.py",
            "ruby -i -pe x src/app.py",
            "yq -i .a=1 src/config.yml",
        ],
    )
    def test_plan_mode_still_denies_real_in_place_edits(self, command: str) -> None:
        d = shell_containment(_shell(command), worktree_root=WT, plan_mode=True)
        assert d.action == "deny"
        assert "in-place" in d.reason


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
    def test_blocks_when_work_and_no_done_marker(self, tmp_path: Path) -> None:
        # Commits ahead of base + no done marker + no nudge -> nudge to run `done`.
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path, commits_ahead=1)
        assert d.action == "deny"  # deny == block the stop
        assert "done" in d.reason
        assert "PR-SUMMARY" not in d.reason  # split-brain gone

    def test_allows_when_no_commits_ahead(self, tmp_path: Path) -> None:
        # No authored work to finalize (#318 higher-signal condition).
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path, commits_ahead=0)
        assert d.action == "allow"

    def test_allows_when_done_marker_present(self, tmp_path: Path) -> None:
        # The session was already finalized via `done` — same completion fact.
        d = session_complete(
            HookEvent(event="stop"),
            worktree_root=tmp_path,
            commits_ahead=1,
            done_marker_present=True,
        )
        assert d.action == "allow"

    def test_allows_when_nudge_marker_present(self, tmp_path: Path) -> None:
        # Tool-agnostic single-shot: once the marker exists (written by the CLI
        # after a prior block), allow even without Claude's stop_hook_active.
        from wade.hooks.policies import stop_nudge_marker_path

        marker = stop_nudge_marker_path(tmp_path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path, commits_ahead=1)
        assert d.action == "allow"

    def test_symlinked_marker_not_trusted(self, tmp_path: Path) -> None:
        # A planted symlink at the marker path must not count as "already nudged".
        from wade.hooks.policies import stop_nudge_marker_path

        real = tmp_path / "elsewhere"
        real.write_text("")
        marker = stop_nudge_marker_path(tmp_path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.symlink_to(real)
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path, commits_ahead=1)
        assert d.action == "deny"

    def test_symlinked_wade_dir_not_trusted(self, tmp_path: Path) -> None:
        # A symlinked .wade directory must not let an outside file skip the nudge.
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "stop-nudged").write_text("")
        (tmp_path / ".wade").symlink_to(outside)
        d = session_complete(HookEvent(event="stop"), worktree_root=tmp_path, commits_ahead=1)
        assert d.action == "deny"

    def test_single_shot_allows_when_already_fired(self, tmp_path: Path) -> None:
        # Work still unfinished, but a Stop hook already fired -> never loop.
        d = session_complete(
            HookEvent(event="stop", stop_hook_active=True),
            worktree_root=tmp_path,
            commits_ahead=1,
        )
        assert d.action == "allow"
