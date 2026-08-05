"""Layering guard: no module under ``src/wade/services/`` may run git directly.

Git operations must go through the ``wade.git`` layer, which provides structured
logging, lock-contention retry (``_run_git_with_retry``), and ``GitError``
signalling. A service that shells out to ``git`` itself bypasses all of that and
re-introduces the drift this guard exists to prevent (see #359).

``gh`` (the GitHub CLI) is explicitly allowed — it is a legitimate service-level
dependency for provider/PR operations.

Detection strategy: parse each service module and flag any list/tuple literal
whose first element is the string ``"git"``. That is the shape of both
``subprocess.run(["git", ...])`` / ``run(["git", ...])`` and the
``cmd = ["git", ...]``-then-execute pattern. Literals inside docstrings are
string constants, not ``ast.List`` nodes, so example text is never flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

import wade.services

_SERVICES_DIR = Path(wade.services.__file__).parent
_SRC_ROOT = _SERVICES_DIR.parent.parent  # .../src, so paths render as wade/...


def _git_argv_literal_lines(tree: ast.AST) -> list[int]:
    """Return line numbers of list/tuple literals whose first element is ``"git"``."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List | ast.Tuple) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "git":
                lines.append(node.lineno)
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
        "it directly (subprocess/run with 'git' as argv[0]). Offending git argv "
        f"literals (file -> line numbers): {offenders}"
    )
