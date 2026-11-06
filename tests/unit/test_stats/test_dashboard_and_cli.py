"""Dashboard HTTP guards/API and the ``wade stats`` CLI."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.stats import ProjectRef, StatsSessionKind
from wade.services import stats_service
from wade.services.stats_dashboard import create_server


@pytest.fixture
def seeded(stats_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(
        stats_service, "resolve_project", lambda _p: ProjectRef(root="/r", slug="o/r")
    )
    from crossby.models.ai import TokenUsage

    t = stats_service.begin_session(
        kind=StatsSessionKind.IMPLEMENT, cwd=tmp_path, ai_tool="claude", model="claude-opus-5.5"
    )
    t.set_usage(TokenUsage(input_tokens=1000, output_tokens=100))
    t.finish(exit_code=0)
    return stats_db


@pytest.fixture
def server(seeded: Path) -> Iterator[tuple[int, str]]:
    srv, _url, token = create_server(0, default_project="o/r")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(srv.server_address[1]), token
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


def _get(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_dashboard_guards(server: tuple[int, str]) -> None:
    port, token = server
    assert _get(port, "/")[0] == 200  # static shell needs no token
    assert _get(port, "/api/report")[0] == 401
    assert _get(port, "/api/report", {"X-Wade-Token": "wrong"})[0] == 401
    assert _get(port, "/api/report", {"X-Wade-Token": token, "Host": "evil.test"})[0] == 403
    bad_origin = {"X-Wade-Token": token, "Origin": "http://evil.test"}
    assert _get(port, "/api/report", bad_origin)[0] == 403


def test_dashboard_api(server: tuple[int, str]) -> None:
    port, token = server
    h = {"X-Wade-Token": token}
    status, body = _get(port, "/api/options", h)
    opts = json.loads(body)
    assert status == 200 and opts["default_project"] == "o/r" and opts["tools"] == ["claude"]
    status, body = _get(port, "/api/report?since=all&tool=claude", h)
    report = json.loads(body)
    assert report["totals"]["sessions"] == 1
    assert report["by_tool"][0]["totals"]["cache_ratio"] is None  # no cache reads reported
    assert report["totals"]["fresh_tokens"] == 1100
    status, body = _get(port, "/api/sessions?since=all", h)
    assert json.loads(body)[0]["ai_tool"] == "claude"
    status, body = _get(port, f"/api/export.csv?since=all&token={token}")
    assert status == 200 and body.startswith(b"id,project,kind")
    assert _get(port, "/api/report?since=bogus", h)[0] == 400


def test_cli_summary_info_sessions_export(seeded: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["stats", "-A", "--since", "all", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output[res.output.index("{") :])
    assert payload["totals"]["sessions"] == 1

    res = runner.invoke(app, ["stats", "info", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output[res.output.index("{") :])["counts"]["ai_sessions"] == 1

    res = runner.invoke(app, ["stats", "sessions", "-A", "--since", "all"])
    assert res.exit_code == 0 and "claude" in res.output

    out = tmp_path / "s.csv"
    res = runner.invoke(app, ["stats", "export", "-A", "-o", str(out)])
    assert res.exit_code == 0 and out.read_text().startswith("id,")

    res = runner.invoke(app, ["stats", "-A", "--since", "nonsense"])
    assert res.exit_code == 2
