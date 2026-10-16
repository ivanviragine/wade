"""Test-only launcher pieces for real ClickUp CLI lifecycle contracts.

The launcher replaces only process boundaries a deterministic test cannot reach:
ClickUp's HTTP transport (a stateful JSON fixture), terminal interactivity for the
existing merge menu, and Codex's plan-capability selection. The real CLI, the
real ``ClickUpProvider``, real Git repositories and the stateful mock ``gh`` run
unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

DEFAULT_STATUSES = [
    {"status": "to do", "type": "open"},
    {"status": "in progress", "type": "custom"},
    {"status": "closed", "type": "closed"},
]


def write_launcher(mock_bin: Path) -> None:
    """Install a ``wade`` launcher that applies the test-only shims first."""
    source = Path(__file__).resolve().parents[2]
    launcher = mock_bin / "wade"
    launcher.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import sys",
                f"sys.path.insert(0, {str(source)!r})",
                "from tests.e2e._clickup_support import main",
                "main()",
                "",
            ]
        )
    )
    launcher.chmod(0o755)


def seed_state(path: Path, tasks: dict[str, dict[str, Any]], **extra: Any) -> None:
    """Write the ClickUp fixture state: tasks, home-List statuses, created IDs."""
    state: dict[str, Any] = {
        "tasks": tasks,
        "statuses": DEFAULT_STATUSES,
        "created_ids": ["abc1234", "def5678", "parent789"],
        "comments": [],
        "tags": [],
    }
    state.update(extra)
    path.write_text(json.dumps(state))


def make_task(
    task_id: str,
    name: str,
    body: str = "## Tasks\nPreserve exact identity.\n",
    *,
    status: str = "to do",
    status_type: str = "open",
    tags: tuple[str, ...] = ("feature-plan",),
) -> dict[str, Any]:
    """One ClickUp task payload with realistic status and home-List metadata."""
    return {
        "id": task_id,
        "name": name,
        "markdown_description": body,
        "status": {"status": status, "type": status_type},
        "tags": [{"name": tag} for tag in tags],
        "list": {"id": "list-1"},
        "url": f"https://app.clickup.com/t/{task_id}",
    }


def read_state(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _respond(request: httpx.Request) -> httpx.Response:
    state_path = Path(os.environ["WADE_MOCK_CLICKUP_STATE"])
    log_path = Path(os.environ["WADE_MOCK_CLICKUP_LOG"])
    state = read_state(state_path)
    payload = json.loads(request.content) if request.content else {}
    parts = [unquote(part) for part in request.url.path.split("/") if part]
    with log_path.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "method": request.method,
                    "parts": parts,
                    "query": dict(request.url.params),
                    "payload": payload,
                }
            )
            + "\n"
        )
    if f"{request.method} {'/'.join(parts[2:4])}" in state.get("fail", []):
        return httpx.Response(500, json={"err": "fixture outage"})
    tasks: dict[str, Any] = state["tasks"]
    data: dict[str, Any] = {}
    code = 200
    if parts[:3] == ["api", "v2", "task"] and len(parts) >= 4:
        task_id = parts[3]
        if task_id not in tasks:
            return httpx.Response(404, json={"err": "Task not found", "ECODE": "ITEM_015"})
        task = tasks[task_id]
        if len(parts) == 4:
            if request.method == "PUT":
                for key, value in payload.items():
                    if key == "status":
                        kind = next(
                            (s["type"] for s in state["statuses"] if s["status"] == value),
                            "custom",
                        )
                        task["status"] = {"status": value, "type": kind}
                    else:
                        task[key] = value
            data = task
        elif parts[4] == "tag" and len(parts) == 6:
            tag = parts[5]
            if request.method == "POST" and not any(t["name"] == tag for t in task["tags"]):
                task["tags"].append({"name": tag})
            elif request.method == "DELETE":
                task["tags"] = [t for t in task["tags"] if t["name"] != tag]
        elif parts[4] == "comment":
            state.setdefault("comments", []).append({"id": task_id, **payload})
        else:
            code = 404
    elif parts[:3] == ["api", "v2", "team"] and parts[-1] == "task":
        tag = request.url.params.get("tags[]")
        include_closed = request.url.params.get("include_closed") == "true"
        selected = [
            t
            for t in tasks.values()
            if (not tag or any(x["name"] == tag for x in t["tags"]))
            and (include_closed or t["status"]["type"] != "closed")
        ]
        page = int(request.url.params.get("page", "0"))
        size = int(state.get("page_size", 100))
        data = {"tasks": selected[page * size : (page + 1) * size]}
    elif parts[:3] == ["api", "v2", "space"] and parts[-1] == "tag":
        if request.method == "POST":
            state.setdefault("tags", []).append(payload["tag"])
        data = {"tags": state.get("tags", [])}
    elif parts[:3] == ["api", "v2", "list"] and len(parts) == 4:
        data = {"id": parts[3], "space": {"id": "space-1"}, "statuses": state["statuses"]}
    elif parts[:3] == ["api", "v2", "list"] and parts[-1] == "task" and request.method == "POST":
        task_id = state["created_ids"].pop(0)
        data = make_task(
            task_id,
            payload["name"],
            payload.get("markdown_description", ""),
            tags=tuple(payload.get("tags", [])),
        )
        tasks[task_id] = data
    else:
        code = 404
    state_path.write_text(json.dumps(state))
    return httpx.Response(code, json=data)


def _install_transport() -> None:
    client = httpx.Client

    def factory(*args: Any, **kwargs: Any) -> httpx.Client:
        assert str(kwargs.get("base_url", "")) == "https://api.clickup.com"
        return client(*args, **kwargs, transport=httpx.MockTransport(_respond))

    httpx.Client = factory  # type: ignore[misc]


def _install_terminal() -> None:
    """Drive the existing interactive menus: pick the labeled option, confirm."""
    label = os.environ.get("WADE_MOCK_TTY_SELECT")
    if not label:
        return
    from wade.ui import prompts

    def select(_message: str, options: list[str], *args: Any, **kwargs: Any) -> int:
        return options.index(label)

    prompts.is_tty = lambda: True  # type: ignore[assignment]
    prompts.select = select  # type: ignore[assignment]
    prompts.confirm = lambda *args, **kwargs: True  # type: ignore[assignment]


def _install_codex_collector() -> None:
    """Keep the native plan collector lane, as the GitHub plan contract does."""
    from crossby.ai_tools.codex import CodexAdapter
    from crossby.models.ai import PlanModeActivation

    original = CodexAdapter.capabilities

    def collected(self: CodexAdapter) -> Any:
        caps = original(self)
        return caps.model_copy(
            update={
                "plan_mode": caps.plan_mode.model_copy(
                    update={"activation": PlanModeActivation.UNSUPPORTED}
                )
            }
        )

    CodexAdapter.capabilities = collected  # type: ignore[method-assign]


def main() -> None:
    _install_transport()
    _install_terminal()
    _install_codex_collector()
    from wade.cli.main import cli_main

    cli_main()
