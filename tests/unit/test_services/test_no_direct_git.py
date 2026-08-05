"""Layering guard: no module under ``src/wade/services/`` may run git directly.

Git operations must go through the ``wade.git`` layer, which provides structured
logging, lock-contention retry (``_run_git_with_retry``), and ``GitError``
signalling. A service that shells out to ``git`` itself bypasses all of that and
re-introduces the drift this guard exists to prevent (see #359).

``gh`` (the GitHub CLI) is explicitly allowed — it is a legitimate service-level
dependency for provider/PR operations.

Detection strategy: parse each service module and flag two literal shapes:

1. A list/tuple literal whose first element resolves to the git executable —
   the shape of ``subprocess.run(["git", ...])`` / ``run(["git", ...])`` and the
   ``cmd = ["git", ...]``-then-execute pattern. An absolute path to the binary
   (``["/usr/bin/git", ...]``) is matched by basename.
2. A string command argument to a process call (``subprocess.run`` / ``Popen`` /
   ``call`` / ``check_output`` / ``check_call`` / ``os.system``) whose first
   shell token resolves to git — the shape of
   ``subprocess.run("git status", shell=True)`` or a call using an absolute path
   to the executable.

String constants are only inspected when they are the command argument of a
process call, so example text in docstrings and log messages is never flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

import wade.services

_SERVICES_DIR = Path(wade.services.__file__).parent
_SRC_ROOT = _SERVICES_DIR.parent.parent  # .../src, so paths render as wade/...

# Callables that spawn a subprocess; we inspect their command (first positional
# or ``args=``) argument for a git invocation expressed as a string.
_PROCESS_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output", "system"})


def _looks_like_git_executable(value: object) -> bool:
    """True if ``value`` is a git shell command or a path to the git binary.

    Handles bare ``"git"``, ``"git status --porcelain"``, and absolute/relative
    paths to the executable (``"/usr/bin/git"``) via basename comparison. Tokens
    like ``"github"`` or ``"gitignore"`` are not git and are left alone.
    """
    if not isinstance(value, str):
        return False
    tokens = value.strip().split()
    if not tokens:
        return False
    return PurePosixPath(tokens[0]).name == "git"


def _first_elt_is_git(node: ast.AST) -> bool:
    """True if ``node`` is a list/tuple literal whose first element is git argv[0]."""
    if isinstance(node, ast.List | ast.Tuple) and node.elts:
        first = node.elts[0]
        return isinstance(first, ast.Constant) and _looks_like_git_executable(first.value)
    return False


def _process_call_command(node: ast.Call) -> ast.expr | None:
    """Return the command argument of a subprocess/os process call, else ``None``."""
    func = node.func
    if isinstance(func, ast.Attribute):
        name: str | None = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        name = None
    if name not in _PROCESS_CALLS:
        return None
    if node.args:
        return node.args[0]
    for kw in node.keywords:
        if kw.arg == "args":
            return kw.value
    return None


def _git_argv_literal_lines(tree: ast.AST) -> list[int]:
    """Return line numbers of literal git invocations (argv lists or command strings)."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List | ast.Tuple):
            if _first_elt_is_git(node):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call):
            command = _process_call_command(node)
            if isinstance(command, ast.Constant) and _looks_like_git_executable(command.value):
                lines.append(command.lineno)
    return lines


def test_no_service_module_shells_out_to_git_directly() -> None:
    offenders: dict[str, list[int]] = {}
    for path in sorted(_SERVICES_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _git_argv_literal_lines(tree)
        if hits:
            offenders[str(path.relative_to(_SRC_ROOT))] = hits

    assert not offenders, (
        "Service modules must route git through the wade.git layer, not execute "
        "it directly (subprocess/run with 'git' as argv[0], or a 'git ...' command "
        f"string). Offending git literals (file -> line numbers): {offenders}"
    )


def test_git_argv_detection_catches_string_and_path_forms() -> None:
    """The guard flags shell-string and executable-path git calls, not just argv lists.

    Also asserts zero false positives: a module docstring mentioning git and
    allowed ``gh`` calls must not be flagged.
    """
    source = '''\
"""A module docstring mentioning git status must never be flagged."""
import subprocess


def allowed() -> None:
    subprocess.run(["gh", "pr", "view"])
    subprocess.run("gh pr view", shell=True)


def offenders() -> None:
    subprocess.run(["git", "status"])
    subprocess.run(("git", "log"))
    subprocess.run("git status", shell=True)
    subprocess.run(["/usr/bin/git", "status"])
    subprocess.Popen("/usr/bin/git log", shell=True)
'''
    tree = ast.parse(source)
    hits = _git_argv_literal_lines(tree)
    # Five offending git call sites; the docstring and gh calls are not flagged.
    assert len(hits) == 5
