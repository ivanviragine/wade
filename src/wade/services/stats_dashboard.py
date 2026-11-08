"""Loopback HTTP server for the ``wade stats ui`` dashboard.

Dependency-free (stdlib ``ThreadingHTTPServer``) and read-only: the API only
reads the local stats database. It still follows the same hostile-browser
posture as other local WADE/Crossby servers, because usage data (task IDs,
branch names, project paths) is private:

- binds ``127.0.0.1`` only;
- every ``/api/*`` request needs the per-run ``secrets`` token (header
  ``X-Wade-Token`` or ``?token=`` for download links), compared in constant time;
- the ``Host`` header must be the loopback address the server is bound to,
  which defeats DNS rebinding;
- a cross-origin ``Origin`` is rejected.

The static page (``templates/stats/dashboard.html``) holds no data and is
served without the token; it reads the token from its own URL fragment.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import socket
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from wade.models.stats import BreakdownRow, StatsFilter, StatsReport, TaskRow, UsageTotals
from wade.services import stats_service
from wade.utils.templates import get_templates_dir

HOST = "127.0.0.1"
MAX_SESSIONS = 2000


def _totals_json(t: UsageTotals | None) -> dict[str, Any] | None:
    return None if t is None else t.as_dict()


def _rows_json(rows: list[BreakdownRow]) -> list[dict[str, Any]]:
    return [{"key": r.key, "totals": r.totals.as_dict()} for r in rows]


def _task_json(r: TaskRow) -> dict[str, Any]:
    data = r.model_dump(mode="json")
    data["totals"] = r.totals.as_dict()
    return data


def report_json(report: StatsReport) -> dict[str, Any]:
    """Report as JSON, with derived ratios (cache share, failure rate…) inlined."""
    return {
        "generated_at": report.generated_at.isoformat(),
        "filter": report.filter.model_dump(mode="json"),
        "totals": _totals_json(report.totals),
        "previous_totals": _totals_json(report.previous_totals),
        "by_tool": _rows_json(report.by_tool),
        "by_model": _rows_json(report.by_model),
        "by_kind": _rows_json(report.by_kind),
        "by_project": _rows_json(report.by_project),
        "daily": [d.model_dump(mode="json") for d in report.daily],
        "top_tasks": [_task_json(t) for t in report.top_tasks],
        "rework_by_model": [r.as_dict() for r in report.rework_by_model],
        "tasks_created": report.tasks_created,
        "tasks_closed": report.tasks_closed,
        "prs": report.prs.model_dump(mode="json"),
        "unpriced_models": report.unpriced_models,
        "insights": [i.model_dump(mode="json") for i in report.insights],
    }


def filter_from_query(query: dict[str, list[str]]) -> StatsFilter:
    def many(name: str) -> list[str]:
        return [v for raw in query.get(name, []) for v in raw.split(",") if v]

    def one(name: str) -> str | None:
        vals = query.get(name)
        return vals[0] if vals and vals[0] else None

    return StatsFilter(
        since=stats_service.parse_time_bound(one("since")),
        until=stats_service.parse_time_bound(one("until")),
        projects=many("project"),
        tools=many("tool"),
        models=many("model"),
        kinds=many("kind"),
        task_id=one("task"),
    )


def make_handler(
    token: str, port_ref: list[int], default_project: str | None
) -> type[BaseHTTPRequestHandler]:
    page_path = get_templates_dir() / "stats" / "dashboard.html"

    class Handler(BaseHTTPRequestHandler):
        server_version = "wade-stats"

        def log_message(self, format: str, *args: Any) -> None:
            return  # keep the terminal quiet

        # -- guards -------------------------------------------------------

        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            return host in {f"{HOST}:{port_ref[0]}", f"localhost:{port_ref[0]}"}

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin in {
                f"http://{HOST}:{port_ref[0]}",
                f"http://localhost:{port_ref[0]}",
            }

        def _token_ok(self, query: dict[str, list[str]]) -> bool:
            supplied = self.headers.get("X-Wade-Token") or (query.get("token") or [""])[0]
            return secrets.compare_digest(supplied.encode(), token.encode())

        def _send(self, status: HTTPStatus, body: bytes, ctype: str, **headers: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in headers.items():
                self.send_header(key.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, default=str).encode()
            self._send(status, body, "application/json; charset=utf-8")

        # -- routes -------------------------------------------------------

        def do_GET(self) -> None:
            if not self._host_ok() or not self._origin_ok():
                self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
                return
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if parts.path in ("/", "/index.html"):
                try:
                    html = page_path.read_bytes()
                except OSError:
                    self._send(HTTPStatus.NOT_FOUND, b"dashboard asset missing", "text/plain")
                    return
                self._send(
                    HTTPStatus.OK,
                    html,
                    "text/html; charset=utf-8",
                    Content_Security_Policy=(
                        "default-src 'none'; script-src 'unsafe-inline'; "
                        "style-src 'unsafe-inline'; connect-src 'self'; img-src data:; "
                        "frame-ancestors 'none'"
                    ),
                )
                return
            if not parts.path.startswith("/api/"):
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            if not self._token_ok(query):
                self._send(HTTPStatus.UNAUTHORIZED, b"unauthorized", "text/plain")
                return
            try:
                self._api(parts.path, query)
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover - defensive
                self._json(
                    {"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR
                )

        def _api(self, path: str, query: dict[str, list[str]]) -> None:
            if path == "/api/options":
                opts: dict[str, Any] = dict(stats_service.filter_options())
                opts["default_project"] = (
                    default_project if default_project in opts.get("projects", []) else None
                )
                opts["db_path"] = stats_service.store_info()["path"]
                self._json(opts)
                return
            flt = filter_from_query(query)
            if path == "/api/report":
                self._json(report_json(stats_service.build_stats_report(flt)))
            elif path == "/api/sessions":
                rows = stats_service.session_rows(stats_service.list_sessions(flt))
                rows.reverse()
                self._json(rows[:MAX_SESSIONS])
            elif path == "/api/prs":
                prs = stats_service.list_pull_requests(flt)
                self._json(
                    [
                        {**pr.model_dump(mode="json"), "lead_time_h": pr.lead_time_h}
                        for pr in reversed(prs)
                    ]
                )
            elif path == "/api/export.csv":
                body = stats_service.export_sessions(flt, "csv").encode()
                self._send(
                    HTTPStatus.OK,
                    body,
                    "text/csv; charset=utf-8",
                    Content_Disposition='attachment; filename="wade-sessions.csv"',
                )
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    return Handler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def create_server(
    port: int = 0, *, default_project: str | None = None
) -> tuple[ThreadingHTTPServer, str, str]:
    """Build (but don't start) the server; returns (server, url, token)."""
    token = secrets.token_urlsafe(24)
    port_ref = [port or _free_port()]
    server = ThreadingHTTPServer(
        (HOST, port_ref[0]), make_handler(token, port_ref, default_project)
    )
    port_ref[0] = int(server.server_address[1])
    url = f"http://{HOST}:{port_ref[0]}/#token={token}"
    return server, url, token


def serve(
    *,
    port: int = 0,
    open_browser: bool = True,
    default_project: str | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """Run the dashboard until Ctrl+C."""
    server, url, _ = create_server(port, default_project=default_project)
    log(f"WADE stats dashboard: {url}")
    log("Press Ctrl+C to stop.")
    if open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
