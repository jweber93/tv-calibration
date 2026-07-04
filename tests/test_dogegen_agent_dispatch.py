"""
Tests for the Dogegen Companion Agent proxy dispatch in server.py (#585b/e).

When DOGEGEN_AGENT_URL / the agent_url config field is set, _dogegen_status_payload,
_start_dogegen_for_session, and _stop_dogegen must short-circuit the entire local
Popen/tasklist/pgrep path and proxy to the agent over HTTP instead. These tests mock
httpx so no real agent needs to be running, and assert the local subprocess path is
never touched while an agent URL is configured.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import server as srv

client = TestClient(srv.app)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_httpx_get(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from httpx import HTTPStatusError
        resp.raise_for_status.side_effect = HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock(text="agent error")
        )
    return resp


def _mock_httpx_post(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from httpx import HTTPStatusError
        resp.raise_for_status.side_effect = HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock(text="agent error")
        )
    return resp


@pytest.fixture(autouse=True)
def _reset_state():
    srv._dogegen_agent.set("")
    srv._dogegen_state.proc = None
    srv._dogegen_state.started_at = None
    srv._dogegen_state.launch_cmd = []
    srv._dogegen_state.last_error = None
    srv._dogegen_config = {"path": "", "resolve_host": "", "window_pct": 10, "maxcll": 1000}
    yield
    srv._dogegen_agent.set("")


@pytest.fixture
def session_id():
    resp = client.post("/api/session", json={"tv_key": "u8g"})
    assert resp.status_code == 200
    return resp.json()["id"]


# ── GET /api/dogegen/status via agent ──────────────────────────────────────────

class TestDogegenStatusViaAgent:
    def test_proxies_agent_payload(self):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        agent_resp = {
            "configured": True,
            "path": "C:\\Dogegen\\Dogegen.exe",
            "running": True,
            "pid": 4321,
            "managed": True,
            "ready": True,
            "ready_in_ms": 0,
            "resolve_host": "127.0.0.1",
            "window_pct": 10,
            "maxcll": 1000,
            "last_error": None,
            "launch_cmd": ["C:\\Dogegen\\Dogegen.exe"],
        }
        with patch("httpx.get", return_value=_mock_httpx_get(agent_resp)):
            r = client.get("/api/dogegen/status")
        assert r.status_code == 200
        d = r.json()
        assert d["running"] is True
        assert d["pid"] == 4321
        assert d["agent_url"] == "http://192.168.1.50:7071"
        assert d["agent_reachable"] is True

    def test_proxies_to_correct_url(self):
        srv._dogegen_agent.set("http://10.0.0.5:7071")
        captured = {}

        def mock_get(url, **kwargs):
            captured["url"] = url
            return _mock_httpx_get({"running": False, "configured": False})

        with patch("httpx.get", side_effect=mock_get):
            client.get("/api/dogegen/status")
        assert captured["url"] == "http://10.0.0.5:7071/status"

    def test_agent_connect_error_returns_unreachable_payload(self):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            r = client.get("/api/dogegen/status")
        assert r.status_code == 200
        d = r.json()
        assert d["running"] is False
        assert d["agent_reachable"] is False
        assert "Cannot reach" in d["last_error"]

    def test_agent_timeout_returns_unreachable_payload(self):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        import httpx
        with patch("httpx.get", side_effect=httpx.ReadTimeout("timed out")):
            r = client.get("/api/dogegen/status")
        assert r.status_code == 200
        d = r.json()
        assert d["agent_reachable"] is False
        assert "timed out" in d["last_error"].lower()

    def test_does_not_touch_local_process_discovery(self, monkeypatch):
        """Short-circuit contract (#585b): _find_dogegen_executable /
        _external_dogegen_pid must not run at all when an agent is configured."""
        srv._dogegen_agent.set("http://192.168.1.50:7071")

        def boom():
            raise AssertionError("local dogegen discovery must not run in agent mode")

        monkeypatch.setattr(srv, "_find_dogegen_executable", boom)
        monkeypatch.setattr(srv, "_external_dogegen_pid", boom)

        with patch("httpx.get", return_value=_mock_httpx_get({"running": False, "configured": False})):
            r = client.get("/api/dogegen/status")
        assert r.status_code == 200

    def test_empty_agent_url_uses_local_path(self):
        srv._dogegen_agent.set("")
        r = client.get("/api/dogegen/status")
        assert r.status_code == 200
        d = r.json()
        assert d["agent_url"] == ""


# ── POST /api/dogegen/config — agent_url field ─────────────────────────────────

class TestDogegenConfigAgentUrl:
    def test_sets_and_persists_agent_url(self, tmp_path):
        # Once agent_url is set, the endpoint's own response computes status via
        # the (now-configured) agent, so the agent HTTP call must be mocked too.
        with patch("server._PREFS_PATH", tmp_path / ".prefs.json"), \
             patch("httpx.get", return_value=_mock_httpx_get({"running": False, "configured": False})):
            r = client.post("/api/dogegen/config", json={"agent_url": "http://192.168.1.50:7071"})
            assert r.status_code == 200
            assert srv._dogegen_agent.get() == "http://192.168.1.50:7071"

            saved = srv._prefs.get("dogegen_agent_url")
            assert saved == "http://192.168.1.50:7071"

    def test_strips_trailing_slash(self):
        with patch("httpx.get", return_value=_mock_httpx_get({"running": False, "configured": False})):
            client.post("/api/dogegen/config", json={"agent_url": "http://192.168.1.50:7071/"})
        assert srv._dogegen_agent.get() == "http://192.168.1.50:7071"

    def test_clearing_agent_url_reverts_to_local(self):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        client.post("/api/dogegen/config", json={"agent_url": ""})
        assert srv._dogegen_agent.get() == ""

    @pytest.mark.parametrize("bad_url", [
        "not-a-url",
        "192.168.1.50:7071",
        "ftp://192.168.1.50:7071",
        "http://",
    ])
    def test_rejects_malformed_agent_url(self, bad_url):
        srv._dogegen_agent.set("")
        r = client.post("/api/dogegen/config", json={"agent_url": bad_url})
        assert r.status_code == 400
        assert "valid http" in r.json()["detail"]
        assert srv._dogegen_agent.get() == ""

    def test_accepts_https_agent_url(self):
        with patch("httpx.get", return_value=_mock_httpx_get({"running": False, "configured": False})):
            r = client.post("/api/dogegen/config", json={"agent_url": "https://agent.example.com:7071"})
        assert r.status_code == 200
        assert srv._dogegen_agent.get() == "https://agent.example.com:7071"


# ── POST /api/session/{sid}/dogegen/start via agent ────────────────────────────

class TestDogegenStartViaAgent:
    def test_proxies_start_with_session_mode(self, session_id):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})

        captured = {}

        def mock_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return _mock_httpx_post({
                "ok": True, "already_running": False,
                "running": True, "configured": True, "pid": 999,
                "managed": True, "ready": False, "ready_in_ms": 2000,
                "resolve_host": "", "window_pct": 10, "maxcll": 1000,
                "last_error": None, "launch_cmd": ["Dogegen.exe"],
            })

        with patch("httpx.post", side_effect=mock_post):
            r = client.post(f"/api/session/{session_id}/dogegen/start")

        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["running"] is True
        assert d["agent_url"] == "http://192.168.1.50:7071"
        assert captured["url"] == "http://192.168.1.50:7071/start"
        assert captured["json"] == {"mode": "HDR10"}

    def test_agent_unreachable_raises_502(self, session_id):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            r = client.post(f"/api/session/{session_id}/dogegen/start")
        assert r.status_code == 502
        assert "Cannot reach" in r.json()["detail"]

    def test_agent_timeout_raises_502(self, session_id):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectTimeout("timed out")):
            r = client.post(f"/api/session/{session_id}/dogegen/start")
        assert r.status_code == 502
        assert "timed out" in r.json()["detail"].lower()

    def test_does_not_launch_local_subprocess(self, session_id, monkeypatch):
        srv._dogegen_agent.set("http://192.168.1.50:7071")

        def boom(*a, **kw):
            raise AssertionError("local subprocess.Popen must not run in agent mode")

        monkeypatch.setattr(srv.subprocess, "Popen", boom)
        monkeypatch.setattr(srv, "_find_dogegen_executable", boom)

        with patch("httpx.post", return_value=_mock_httpx_post({"ok": True, "already_running": False})):
            r = client.post(f"/api/session/{session_id}/dogegen/start")
        assert r.status_code == 200


# ── POST /api/dogegen/stop via agent ────────────────────────────────────────────

class TestDogegenStopViaAgent:
    def test_proxies_stop(self):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        captured = {}

        def mock_post(url, **kwargs):
            captured["url"] = url
            return _mock_httpx_post({
                "ok": True, "already_stopped": False,
                "running": False, "configured": True, "pid": None,
                "managed": False, "ready": False, "ready_in_ms": 0,
                "resolve_host": "", "window_pct": 10, "maxcll": 1000,
                "last_error": None, "launch_cmd": [],
            })

        with patch("httpx.post", side_effect=mock_post):
            r = client.post("/api/dogegen/stop")

        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["running"] is False
        assert captured["url"] == "http://192.168.1.50:7071/stop"

    def test_agent_unreachable_raises_502(self):
        srv._dogegen_agent.set("http://192.168.1.50:7071")
        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            r = client.post("/api/dogegen/stop")
        assert r.status_code == 502
        assert "Cannot reach" in r.json()["detail"]

    def test_does_not_touch_local_state(self, monkeypatch):
        srv._dogegen_agent.set("http://192.168.1.50:7071")

        def boom():
            raise AssertionError("local _managed_dogegen_is_running must not run in agent mode")

        monkeypatch.setattr(srv, "_managed_dogegen_is_running", boom)

        with patch("httpx.post", return_value=_mock_httpx_post({"ok": True, "already_stopped": True})):
            r = client.post("/api/dogegen/stop")
        assert r.status_code == 200


# ── _DogegenAgentState + prefs round-trip ──────────────────────────────────────

class TestDogegenAgentState:
    def test_get_set(self):
        state = srv._DogegenAgentState("http://initial:7071")
        assert state.get() == "http://initial:7071"
        state.set("http://updated:7071")
        assert state.get() == "http://updated:7071"

    def test_load_prefs_restores_agent_url(self, tmp_path):
        prefs_path = tmp_path / ".prefs.json"
        prefs_path.write_text('{"dogegen_agent_url": "http://saved-host:7071"}')
        srv._dogegen_agent.set("")
        with patch("server._PREFS_PATH", prefs_path):
            srv._load_prefs()
        assert srv._dogegen_agent.get() == "http://saved-host:7071"

    def test_save_prefs_writes_agent_url(self, tmp_path):
        prefs_path = tmp_path / ".prefs.json"
        srv._dogegen_agent.set("http://save-me:7071")
        with patch("server._PREFS_PATH", prefs_path):
            srv._save_prefs()
        import json
        data = json.loads(prefs_path.read_text())
        assert data["dogegen_agent_url"] == "http://save-me:7071"
