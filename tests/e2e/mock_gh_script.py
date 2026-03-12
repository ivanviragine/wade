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
        "review_threads": {},
    }


def _load_state() -> dict[str, object]:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
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


def _find_form_value(args: list[str], key: str) -> str | None:
    prefix = f"{key}="
    i = 0
    while i < len(args):
        if args[i] in ("-f", "-F") and i + 1 < len(args):
            value = args[i + 1]
            if value.startswith(prefix):
                return value[len(prefix) :]
            i += 2
            continue
        i += 1
    return None


def _issue_labels_as_json(labels: list[str]) -> list[dict[str, str]]:
    return [{"name": name, "color": "ededed", "description": ""} for name in labels]


def _review_threads_as_graphql_nodes(raw_threads: object) -> list[dict[str, object]]:
    if not isinstance(raw_threads, list):
        return []

    nodes: list[dict[str, object]] = []
    for raw_thread in raw_threads:
        if not isinstance(raw_thread, dict):
            continue

        comments_raw = raw_thread.get("comments", [])
        if isinstance(comments_raw, dict):
            comments_raw = comments_raw.get("nodes", [])
        if not isinstance(comments_raw, list):
            comments_raw = []

        comment_nodes: list[dict[str, object]] = []
        for raw_comment in comments_raw:
            if not isinstance(raw_comment, dict):
                continue
            author_raw = raw_comment.get("author")
            if isinstance(author_raw, dict):
                author = {"login": str(author_raw.get("login", ""))}
            else:
                author_name = str(author_raw or "")
                author = {"login": author_name} if author_name else None

            comment_nodes.append(
                {
                    "body": str(raw_comment.get("body", "")),
                    "path": raw_comment.get("path"),
                    "line": raw_comment.get("line"),
                    "author": author,
                    "createdAt": raw_comment.get("createdAt"),
                    "url": raw_comment.get("url"),
                }
            )

        nodes.append(
            {
                "id": str(raw_thread.get("id", "")),
                "isResolved": bool(raw_thread.get("isResolved", False)),
                "isOutdated": bool(raw_thread.get("isOutdated", False)),
                "comments": {"nodes": comment_nodes},
            }
        )
    return nodes


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
        raw = issues.get(target)
        if not isinstance(raw, dict):
            print(f"Issue not found: {target}", file=sys.stderr)
            return 1
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
        raw = issues.get(target)
        if not isinstance(raw, dict):
            print(f"Issue not found: {target}", file=sys.stderr)
            return 1
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
        raw = issues.get(target)
        if not isinstance(raw, dict):
            print(f"Issue not found: {target}", file=sys.stderr)
            return 1
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


def _pr_matches_filters(args: list[str], pr_num: str, pr: dict[str, object]) -> bool:
    state_filter = (_find_flag(args, "--state") or "open").lower()
    pr_state = str(pr.get("state", "OPEN")).lower()
    if state_filter != "all" and pr_state != state_filter:
        return False

    head_filter = _find_flag(args, "--head")
    if head_filter and str(pr.get("head", "")) != head_filter:
        return False

    base_filter = _find_flag(args, "--base")
    if base_filter and str(pr.get("base", "")) != base_filter:
        return False

    search = (_find_flag(args, "--search") or "").strip().lower()
    if search:
        haystack = " ".join(
            [
                pr_num,
                str(pr.get("title", "")),
                str(pr.get("body", "")),
                str(pr.get("head", "")),
                str(pr.get("base", "")),
            ]
        ).lower()
        if search not in haystack:
            return False

    return True


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
        _pr_num, pr = _find_pr(prs, target)
        if not pr:
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
        _pr_num, pr = _find_pr(prs, target)
        if not pr:
            return 1
        pr["isDraft"] = False
        _save_state(state)
        return 0

    if action == "merge":
        if len(args) < 2:
            return 1
        target = args[1]
        _pr_num, pr = _find_pr(prs, target)
        if not pr:
            return 1
        pr["state"] = "MERGED"
        _save_state(state)
        return 0

    if action == "list":
        limit_raw = _find_flag(args, "--limit")
        try:
            limit = int(limit_raw) if limit_raw else None
        except ValueError:
            limit = None

        items: list[dict[str, object]] = []
        for pr_num, raw in prs.items():
            if not isinstance(raw, dict):
                continue
            if not _pr_matches_filters(args, pr_num, raw):
                continue
            items.append(
                {
                    "number": int(pr_num),
                    "url": raw.get("url", f"{REPO_URL}/pull/{pr_num}"),
                    "title": raw.get("title", ""),
                    "state": raw.get("state", "OPEN"),
                    "isDraft": bool(raw.get("isDraft", False)),
                    "body": raw.get("body", ""),
                    "headRefName": raw.get("head", ""),
                    "baseRefName": raw.get("base", "main"),
                }
            )

        if limit is not None:
            items = items[:limit]

        print(json.dumps(items))
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


def _query_contains(query: str, *parts: str) -> bool:
    normalized = " ".join(query.split())
    return all(part in normalized for part in parts)


def _has_form_values(args: list[str], *keys: str) -> bool:
    return all(bool((_find_form_value(args, key) or "").strip()) for key in keys)


def _handle_api(args: list[str], state: dict[str, object]) -> int:
    if not args:
        return 1
    if args[0] != "graphql":
        return 1

    query = _find_form_value(args, "query") or ""
    if not query:
        return 1

    # review_service.get_pr_review_threads query
    if (
        _query_contains(
            query,
            "repository(owner: $owner, name: $repo)",
            "pullRequest(number: $pr)",
            "reviewThreads(first:",
        )
        and _has_form_values(args, "owner", "repo", "pr", "query")
    ):
        pr_value = _find_form_value(args, "pr") or "0"
        review_threads = state.setdefault("review_threads", {})
        assert isinstance(review_threads, dict)
        raw_threads = review_threads.get(str(pr_value), [])
        nodes = _review_threads_as_graphql_nodes(raw_threads)
        print(
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                    "nodes": nodes,
                                }
                            }
                        }
                    }
                }
            )
        )
        return 0

    # review_service.resolve_thread mutation
    if (
        _query_contains(
            query,
            "resolveReviewThread(input: {threadId: $threadId})",
        )
        and _has_form_values(args, "threadId", "query")
    ):
        thread_id = _find_form_value(args, "threadId") or ""
        review_threads = state.setdefault("review_threads", {})
        assert isinstance(review_threads, dict)

        is_resolved = False
        for raw_threads in review_threads.values():
            if not isinstance(raw_threads, list):
                continue
            for raw_thread in raw_threads:
                if isinstance(raw_thread, dict) and str(raw_thread.get("id", "")) == thread_id:
                    raw_thread["isResolved"] = True
                    is_resolved = True

        _save_state(state)
        print(
            json.dumps(
                {
                    "data": {
                        "resolveReviewThread": {
                            "thread": {"isResolved": is_resolved}
                        }
                    }
                }
            )
        )
        return 0

    # provider.move_to_in_progress query can return empty project items.
    if (
        _query_contains(
            query,
            "issue(number: $number)",
            "projectItems(first: 10)",
        )
        and _has_form_values(args, "owner", "repo", "number", "query")
    ):
        print(
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "issue": {
                                "projectItems": {"nodes": []}
                            }
                        }
                    }
                }
            )
        )
        return 0

    if (
        _query_contains(
            query,
            "updateProjectV2ItemFieldValue",
            "singleSelectOptionId: $option_id",
        )
        and _has_form_values(args, "project_id", "item_id", "field_id", "option_id", "query")
    ):
        print(
            json.dumps(
                {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "item"}}}}
            )
        )
        return 0

    print("mock-gh: unsupported graphql query", file=sys.stderr)
    return 1


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
    if argv[0] == "api":
        return _handle_api(argv[1:], state)

    print(f"mock-gh: unknown command: {argv[0]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"""
