"""
Tests for the terminal web surface: authentication, visible rejection reasons,
session lifecycle, and the terminal page's static-asset contract.

The asset contract matters because the page is served under a strict
Content-Security-Policy (`script-src 'self'`). Anything the page loads must
therefore come from /static/ (the vendored xterm bundle) — a CDN reference is
blocked by the browser and leaves the page stuck on its spinner with no error.
"""
import asyncio
import re
import time
from pathlib import Path

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.auth import create_session
from app.config import Config
from app.api.app import app
from app.terminal.manager import (
    CONNECT_GRACE_SECONDS,
    MAX_SESSIONS,
    TerminalManager,
    get_terminal_manager,
)
from app.terminal.session import SessionState

TERMINAL_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "terminal.html"


class FakeSession:
    """Stand-in for a PTY/SSH session so no real shell is spawned."""

    def __init__(self, state=SessionState.CONNECTED, outputs=()):
        self.id = "fake-session"
        self.state = state
        self.created_at = 1e9
        self.last_activity = 1e9
        self.rows = 24
        self.cols = 80
        self._outputs = list(outputs)
        self._queue = asyncio.Queue()
        self.written = []
        self.closed = False
        self.resizes = []

    async def read_output(self):
        if self._outputs:
            return self._outputs.pop(0)
        return None

    async def write_input(self, data):
        self.written.append(data)

    def resize(self, rows, cols):
        self.resizes.append((rows, cols))

    def is_alive(self):
        return self.state == SessionState.CONNECTED

    def close(self):
        self.closed = True
        self.state = SessionState.CLOSED


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(Config, "DATABASE_ENABLED", False)
    monkeypatch.setattr(Config, "DATABASE_URL", "")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed_client(client):
    client.cookies.set("jarvis_session", create_session("tester"))
    return client


@pytest.fixture
def manager():
    mgr = get_terminal_manager()
    saved = dict(mgr._sessions)
    yield mgr
    mgr._sessions.clear()
    mgr._sessions.update(saved)


def _close_code(websocket_ctx):
    """The close code the server used, as the browser would observe it."""
    try:
        websocket_ctx.receive_text()
    except WebSocketDisconnect as exc:
        return exc.code
    return None


# ── HTTP endpoints require authentication ─────────────────────


def test_terminal_session_requires_auth(client):
    assert client.post("/api/terminal/session").status_code == 401


def test_terminal_session_list_requires_auth(client):
    assert client.get("/api/terminal/sessions").status_code == 401


def test_terminal_close_requires_auth(client):
    assert client.delete("/api/terminal/whatever").status_code == 401
    assert client.post("/api/terminal/whatever").status_code == 401


def test_terminal_page_redirects_when_unauthenticated(client):
    response = client.get("/terminal", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_terminal_page_served_when_authenticated(authed_client):
    response = authed_client.get("/terminal")
    assert response.status_code == 200
    assert "xterm" in response.text


# ── sendBeacon-compatible close ───────────────────────────────


def test_beacon_post_closes_session(authed_client, manager):
    """navigator.sendBeacon can only POST, so the close route must accept POST."""
    fake = FakeSession()
    manager._sessions[fake.id] = fake

    response = authed_client.post(f"/api/terminal/{fake.id}")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert fake.closed is True
    assert fake.id not in manager._sessions


def test_delete_closes_session(authed_client, manager):
    fake = FakeSession()
    manager._sessions[fake.id] = fake

    assert authed_client.delete(f"/api/terminal/{fake.id}").json()["success"] is True
    assert fake.id not in manager._sessions


def test_close_unknown_session_reports_failure(authed_client):
    body = authed_client.delete("/api/terminal/nope").json()
    assert body["success"] is False
    assert "error" in body


# ── WebSocket rejection reasons must reach JavaScript ─────────


def test_websocket_without_cookie_reports_auth_close_code(client):
    """A pre-accept close is invisible to JS (code 1006), so we accept first."""
    with client.websocket_connect("/ws/terminal/anything") as ws:
        frame = ws.receive_text()
        assert "Authentication required" in frame
        assert _close_code(ws) == 4401


def test_websocket_unknown_session_reports_session_close_code(authed_client):
    with authed_client.websocket_connect("/ws/terminal/does-not-exist") as ws:
        frame = ws.receive_text()
        assert "not found" in frame
        assert _close_code(ws) == 4004


def test_websocket_reports_failed_pty_setup(authed_client, manager):
    dead = FakeSession(state=SessionState.CLOSED)
    manager._sessions[dead.id] = dead

    with authed_client.websocket_connect(f"/ws/terminal/{dead.id}") as ws:
        frame = ws.receive_text()
        assert "Terminal connection failed" in frame
        assert _close_code(ws) == 1011


def test_websocket_streams_output_and_accepts_input(authed_client, manager):
    fake = FakeSession(outputs=["hello\r\n"])
    manager._sessions[fake.id] = fake

    with authed_client.websocket_connect(f"/ws/terminal/{fake.id}") as ws:
        output = ws.receive_text()
        assert "hello" in output
        ws.send_text('{"type":"input","data":"ls\\r"}')
        ws.send_text('{"type":"resize","rows":40,"cols":120}')

        for _ in range(50):
            if fake.written and fake.resizes:
                break
            time.sleep(0.01)

        assert fake.written == ["ls\r"]
        assert fake.resizes == [(40, 120)]

    assert fake.closed is True
    assert fake.id not in manager._sessions


# ── Session reaping ───────────────────────────────────────────


def test_connecting_session_is_not_reaped_inside_grace_period():
    mgr = TerminalManager()
    connecting = FakeSession(state=SessionState.CONNECTING)
    assert mgr._is_reapable(connecting, now=connecting.created_at + 1) is False


def test_stuck_connecting_session_is_reaped_after_grace_period():
    mgr = TerminalManager()
    connecting = FakeSession(state=SessionState.CONNECTING)
    assert mgr._is_reapable(connecting, now=connecting.created_at + CONNECT_GRACE_SECONDS + 1) is True


def test_closed_session_is_reapable():
    mgr = TerminalManager()
    assert mgr._is_reapable(FakeSession(state=SessionState.CLOSED), now=2e9) is True


def test_live_session_is_kept():
    mgr = TerminalManager()
    fake = FakeSession()
    fake.created_at = 2e9
    fake.last_activity = 2e9
    assert mgr._is_reapable(fake, now=2e9) is False


def test_session_limit_error_is_actionable():
    mgr = TerminalManager()
    for index in range(MAX_SESSIONS):
        fake = FakeSession()
        fake.id = f"fake-{index}"
        fake.last_activity = 1e18
        fake.created_at = 1e18
        mgr._sessions[fake.id] = fake

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mgr.create_session())

    assert "Too many active terminal sessions" in str(excinfo.value)
    assert str(MAX_SESSIONS) in str(excinfo.value)


# ── Terminal page asset contract ──────────────────────────────


def _referenced_static_urls() -> list[str]:
    html = TERMINAL_HTML.read_text(encoding="utf-8")
    return re.findall(r'<(?:script|link)[^>]*?(?:src|href)="(/static/[^"]+)"', html)


def test_terminal_page_loads_every_asset_from_this_origin():
    urls = _referenced_static_urls()
    assert urls, "terminal.html should reference its assets explicitly"
    html = TERMINAL_HTML.read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in html, "terminal assets must be self-hosted (CSP blocks third-party scripts)"


def test_terminal_page_assets_all_resolve(client):
    for url in _referenced_static_urls():
        response = client.get(url)
        assert response.status_code == 200, f"{url} must be served locally"


def test_csp_forbids_third_party_scripts(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "http" not in csp.split("script-src", 1)[1]


def test_vendored_xterm_is_actually_the_library(client):
    body = client.get("/static/vendor/xterm/xterm.min.js?v=5.5.0").text
    assert "Terminal" in body
    assert len(body) > 100000
    assert client.get("/static/vendor/xterm/xterm.min.css?v=5.5.0").status_code == 200
    assert client.get("/static/vendor/xterm/addon-fit.min.js?v=0.10.0").status_code == 200
    assert client.get("/static/vendor/xterm/addon-web-links.min.js?v=0.11.0").status_code == 200


def test_service_worker_precaches_terminal_assets():
    sw = (TERMINAL_HTML.parent / "sw.js").read_text(encoding="utf-8")
    assert "/static/vendor/xterm/xterm.min.js" in sw
    assert "cacheFirst" not in sw, "cache-first pinned stale bundles in installed PWAs"
    assert "/ws/" in sw, "the WebSocket path must stay out of the service worker"
