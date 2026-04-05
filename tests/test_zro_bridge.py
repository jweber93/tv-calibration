"""
Tests for ZRO Bridge proxy endpoints in server.py.

These tests mock httpx calls so no real bridge needs to be running.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import sys
from pathlib import Path
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
        from httpx import HTTPStatusError, Request, Response
        resp.raise_for_status.side_effect = HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock()
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
            "error", request=MagicMock(), response=MagicMock(text="bridge error")
        )
    return resp


# ── GET /api/zro/bridge/status ─────────────────────────────────────────────────

class TestZroBridgeStatus:
    def test_not_configured(self):
        srv._zro_bridge_url = ""
        r = client.get("/api/zro/bridge/status")
        assert r.status_code == 200
        d = r.json()
        assert d["configured"] is False
        assert d["ok"] is False
        assert "not configured" in d["error"].lower()

    def test_bridge_ok_pyautogui(self):
        srv._zro_bridge_url = "http://192.168.1.50:7070"
        bridge_resp = {
            "ok": True,
            "backend": "pyautogui",
            "zro_window_found": True,
            "zro_window_title": "ColourSpace ZRO",
        }
        with patch("httpx.get", return_value=_mock_httpx_get(bridge_resp)):
            r = client.get("/api/zro/bridge/status")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["configured"] is True
        assert d["backend"] == "pyautogui"
        assert d["zro_window_found"] is True

    def test_bridge_connect_error(self):
        srv._zro_bridge_url = "http://192.168.1.50:7070"
        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            r = client.get("/api/zro/bridge/status")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert d["configured"] is True
        assert "Cannot reach" in d["error"]

    def test_bridge_ok_remote_control(self):
        srv._zro_bridge_url = "http://127.0.0.1:7070"
        bridge_resp = {
            "ok": True,
            "backend": "remote_control",
            "remote_control_connected": True,
        }
        with patch("httpx.get", return_value=_mock_httpx_get(bridge_resp)):
            r = client.get("/api/zro/bridge/status")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["remote_control_connected"] is True


# ── POST /api/zro/bridge/config ────────────────────────────────────────────────

class TestZroBridgeConfig:
    def test_set_url(self):
        srv._zro_bridge_url = ""
        r = client.post("/api/zro/bridge/config", json={"url": "http://192.168.1.99:7070"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["url"] == "http://192.168.1.99:7070"
        assert srv._zro_bridge_url == "http://192.168.1.99:7070"

    def test_set_url_strips_trailing_slash(self):
        r = client.post("/api/zro/bridge/config", json={"url": "http://192.168.1.99:7070/"})
        assert r.status_code == 200
        assert r.json()["url"] == "http://192.168.1.99:7070"
        assert srv._zro_bridge_url == "http://192.168.1.99:7070"


# ── POST /api/zro/trigger ──────────────────────────────────────────────────────

class TestZroTrigger:
    def test_trigger_not_configured(self):
        srv._zro_bridge_url = ""
        r = client.post("/api/zro/trigger")
        assert r.status_code == 400
        assert "not configured" in r.json()["detail"].lower()

    def test_trigger_ok(self):
        srv._zro_bridge_url = "http://192.168.1.50:7070"
        bridge_resp = {"ok": True, "method": "key", "key": "space", "window": "ColourSpace ZRO"}
        with patch("httpx.post", return_value=_mock_httpx_post(bridge_resp)):
            r = client.post("/api/zro/trigger")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["method"] == "key"

    def test_trigger_bridge_unreachable(self):
        srv._zro_bridge_url = "http://192.168.1.50:7070"
        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            r = client.post("/api/zro/trigger")
        assert r.status_code == 502
        assert "Cannot reach" in r.json()["detail"]

    def test_trigger_bridge_error_response(self):
        srv._zro_bridge_url = "http://192.168.1.50:7070"
        from httpx import HTTPStatusError
        err = HTTPStatusError("502", request=MagicMock(), response=MagicMock(text="ZRO window not found"))
        with patch("httpx.post", side_effect=err):
            r = client.post("/api/zro/trigger")
        assert r.status_code == 502

    def test_trigger_proxies_to_correct_url(self):
        srv._zro_bridge_url = "http://10.0.0.5:7070"
        bridge_resp = {"ok": True, "method": "key"}
        captured = {}
        def mock_post(url, **kwargs):
            captured["url"] = url
            return _mock_httpx_post(bridge_resp)
        with patch("httpx.post", side_effect=mock_post):
            client.post("/api/zro/trigger")
        assert captured["url"] == "http://10.0.0.5:7070/measure"
