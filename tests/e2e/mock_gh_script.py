"""Stateful mock `gh` script source for deterministic E2E tests."""

from __future__ import annotations

MOCK_GH_SCRIPT = """\
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

LOG = Path(os.environ["WADE_MOCK_GH_LOG"])
STATE = Path(os.environ["WADE_MOCK_GH_STATE"])
REPO_URL = "https://github.com/test/e2e-project"


def _log_invocation(argv: list[str]) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(argv))
        f.write("\\n")


def _default_state() -> dict[str, object]:
    return {
        "next_issue": 1,
        "next_pr": 1,
        "issues": {},
        "prs": {},
        "labels": {},
    }


def _load_state() -> dict[str, object]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()


def _save_state(state: dict[str, object]) -> None:
    STATE.write_text(json.dumps(state), encoding="utf-8")


def _find_flag(args: list[str], *names: str) -> str | None:
    for i, arg in enumerate(args):
        if arg in names and i + 1 < len(args):
            return args[i + 1]
    return None


def _find_all_flags(args: list[str], *names: str) -> list[str]:
    values: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in names and i + 1 < len(args):
            values.append(args[i + 1])
            i += 2
            continue
        i += 1
    return values


def _issue_labels_as_json(labels: list[str]) -> list[dict[str, str]]:
    return [{"name": name, "color": "ededed", "description": ""} for name in labels]


def _handle_issue(args: list[str], state: dict[str, object]) -> int:
    if not args:
        return 1
    action = args[0]
    issues = state.setdefault("issues", {})
    assert isinstance(issues, dict)

    if action == "create":
        title = _find_flag(args, "--title", "-t") or "Untitled issue"
        body = _find_flag(args, "--body", "-b") or ""
        body_file = _find_flag(args, "--body-file")
        if body_file:
            try:
                body = Path(body_file).read_text(encoding="utf-8")
            except OSError:
                body = ""
        labels = _find_all_flags(args, "--label", "-l")

        issue_num = int(state.get("next_issue", 1))
        state["next_issue"] = issue_num + 1
        issues[str(issue_num)] = {
            "title": title,
            "body": body,
            "state": "OPEN",
            "labels": labels,
        }
        _save_state(state)
        print(f"{REPO_URL}/issues/{issue_num}")
        return 0

    if action == "list":
        state_filter = (_find_flag(args, "--state") or "open").lower()
        required_label = _find_flag(args, "--label")
        search_query = _find_flag(args, "--search") or ""
        excluded_labels: set[str] = set()
        for token in search_query.split():
            if token.startswith("-label:") and len(token) > len("-label:"):
                excluded_labels.add(token[len("-label:") :])
        items: list[dict[str, object]] = []
        for num, raw in issues.items():
            if not isinstance(raw, dict):
                continue
            raw_state = str(raw.get("state", "OPEN")).upper()
            if state_filter != "all" and raw_state.lower() != state_filter:
                continue
            labels = raw.get("labels", [])
            if not isinstance(labels, list):
                labels = []
            label_names = [str(x) for x in labels]
            if required_label and required_label not in label_names:
                continue
            if excluded_labels and any(lbl in excluded_labels for lbl in label_names):
                continue
            items.append(
                {
                    "number": int(num),
                    "title": raw.get("title", ""),
                    "body": raw.get("body", ""),
                    "state": raw_state,
                    "labels": _issue_labels_as_json(label_names),
                    "url": f"{REPO_URL}/issues/{num}",
                }
            )
        print(json.dumps(items))
        return 0

    if action == "view":
        target = "1"
        if len(args) >= 2 and not args[1].startswith("-"):
            target = args[1]
        raw = issues.get(target, {})
        if not isinstance(raw, dict):
            raw = {}
        labels = raw.get("labels", [])
        if not isinstance(labels, list):
            labels = []
        print(
            json.dumps(
                {
                    "number": int(target),
                    "title": raw.get("title", "Test issue"),
                    "body": raw.get("body", ""),
                    "state": raw.get("state", "OPEN"),
                    "labels": _issue_labels_as_json([str(x) for x in labels]),
                    "url": f"{REPO_URL}/issues/{target}",
                }
            )
        )
        return 0

    if action == "edit":
        if len(args) < 2:
            return 1
        target = args[1]
        raw = issues.setdefault(target, {"title": "", "body": "", "state": "OPEN", "labels": []})
        assert isinstance(raw, dict)
        labels = raw.setdefault("labels", [])
        if not isinstance(labels, list):
            labels = []
            raw["labels"] = labels

        add_labels = _find_all_flags(args, "--add-label")
        for label in add_labels:
            if label not in labels:
                labels.append(label)
        remove_labels = set(_find_all_flags(args, "--remove-label"))
        if remove_labels:
            raw["labels"] = [lbl for lbl in labels if lbl not in remove_labels]

        title = _find_flag(args, "--title")
        if title is not None:
            raw["title"] = title
        body_file = _find_flag(args, "--body-file")
        if body_file:
            try:
                raw["body"] = Path(body_file).read_text(encoding="utf-8")
            except OSError:
                pass

        _save_state(state)
        return 0

    if action == "close":
        if len(args) < 2:
            return 1
        target = args[1]
        raw = issues.setdefault(target, {"title": "", "body": "", "state": "OPEN", "labels": []})
        assert isinstance(raw, dict)
        raw["state"] = "CLOSED"
        _save_state(state)
        return 0

    if action == "comment":
        return 0

    return 1


def _find_pr(
    prs: dict[str, object],
    target: str,
) -> tuple[str | None, dict[str, object] | None]:
    if target in prs and isinstance(prs[target], dict):
        return target, prs[target]
    if "/pull/" in target:
        num = target.rstrip("/").split("/")[-1]
        if num in prs and isinstance(prs[num], dict):
            return num, prs[num]
    for num, raw in prs.items():
        if isinstance(raw, dict) and raw.get("head") == target:
            return num, raw
    return None, None


def _handle_pr(args: list[str], state: dict[str, object]) -> int:
    if not args:
        return 1
    action = args[0]
    prs = state.setdefault("prs", {})
    assert isinstance(prs, dict)

    if action == "create":
        title = _find_flag(args, "--title") or "Untitled PR"
        body = _find_flag(args, "--body") or ""
        base = _find_flag(args, "--base") or "main"
        head = _find_flag(args, "--head") or "unknown"
        draft = "--draft" in args

        pr_num = int(state.get("next_pr", 1))
        state["next_pr"] = pr_num + 1
        pr_url = f"{REPO_URL}/pull/{pr_num}"
        prs[str(pr_num)] = {
            "title": title,
            "body": body,
            "state": "OPEN",
            "isDraft": draft,
            "base": base,
            "head": head,
            "url": pr_url,
        }
        _save_state(state)
        print(pr_url)
        return 0

    if action == "view":
        if len(args) < 2:
            return 1
        target = args[1]
        pr_num, pr = _find_pr(prs, target)
        if not pr_num or not pr:
            return 1
        print(
            json.dumps(
                {
                    "number": int(pr_num),
                    "url": pr.get("url", f"{REPO_URL}/pull/{pr_num}"),
                    "title": pr.get("title", ""),
                    "state": pr.get("state", "OPEN"),
                    "isDraft": bool(pr.get("isDraft", False)),
                    "body": pr.get("body", ""),
                }
            )
        )
        return 0

    if action == "edit":
        if len(args) < 2:
            return 1
        target = args[1]
        pr = prs.get(target)
        if not isinstance(pr, dict):
            return 1
        body = _find_flag(args, "--body")
        if body is not None:
            pr["body"] = body
        _save_state(state)
        return 0

    if action == "ready":
        if len(args) < 2:
            return 1
        target = args[1]
        pr = prs.get(target)
        if not isinstance(pr, dict):
            return 1
        pr["isDraft"] = False
        _save_state(state)
        return 0

    if action == "merge":
        if len(args) < 2:
            return 1
        target = args[1]
        pr = prs.get(target)
        if not isinstance(pr, dict):
            return 1
        pr["state"] = "MERGED"
        _save_state(state)
        return 0

    if action == "list":
        print("[]")
        return 0

    return 1


def _handle_label(args: list[str], state: dict[str, object]) -> int:
    if not args:
        return 1
    action = args[0]
    labels = state.setdefault("labels", {})
    assert isinstance(labels, dict)

    if action == "list":
        search = _find_flag(args, "--search")
        names = [name for name in labels if not search or search in name]
        if "-q" in args:
            if names:
                print("\\n".join(names))
            return 0
        print(json.dumps([{"name": name} for name in names]))
        return 0

    if action == "create":
        if len(args) < 2:
            return 1
        name = args[1]
        labels[name] = {
            "color": _find_flag(args, "--color") or "ededed",
            "description": _find_flag(args, "--description") or "",
        }
        _save_state(state)
        return 0

    return 0


def _handle_repo(args: list[str]) -> int:
    if "-q" in args and ".nameWithOwner" in args:
        print("test/e2e-project")
        return 0
    print(json.dumps({"name": "e2e-project", "owner": {"login": "test"}}))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    _log_invocation(argv)
    if not argv:
        return 0
    state = _load_state()

    if argv[0] == "auth":
        print("Logged in to github.com")
        return 0
    if argv[0] == "issue":
        return _handle_issue(argv[1:], state)
    if argv[0] == "pr":
        return _handle_pr(argv[1:], state)
    if argv[0] == "label":
        return _handle_label(argv[1:], state)
    if argv[0] == "repo":
        return _handle_repo(argv[1:])

    print(f"mock-gh: unknown command: {argv[0]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"""
