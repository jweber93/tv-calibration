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
        srv._zro_bridge.set("")
        r = client.get("/api/zro/bridge/status")
        assert r.status_code == 200
        d = r.json()
        assert d["configured"] is False
        assert d["ok"] is False
        assert "not configured" in d["error"].lower()

    def test_bridge_ok_pyautogui(self):
        srv._zro_bridge.set("http://192.168.1.50:7070")
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
        srv._zro_bridge.set("http://192.168.1.50:7070")
        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            r = client.get("/api/zro/bridge/status")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert d["configured"] is True
        assert "Cannot reach" in d["error"]

    def test_bridge_ok_remote_control(self):
        srv._zro_bridge.set("http://127.0.0.1:7070")
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
        srv._zro_bridge.set("")
        r = client.post("/api/zro/bridge/config", json={"url": "http://192.168.1.99:7070"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["url"] == "http://192.168.1.99:7070"
        assert srv._zro_bridge.get() == "http://192.168.1.99:7070"

    def test_set_url_strips_trailing_slash(self):
        r = client.post("/api/zro/bridge/config", json={"url": "http://192.168.1.99:7070/"})
        assert r.status_code == 200
        assert r.json()["url"] == "http://192.168.1.99:7070"
        assert srv._zro_bridge.get() == "http://192.168.1.99:7070"


# ── GET /api/zro/bridge/instruments, POST /api/zro/bridge/instrument (#531) ────

class TestZroBridgeInstruments:
    def test_list_not_configured(self):
        srv._zro_bridge.set("")
        r = client.get("/api/zro/bridge/instruments")
        assert r.status_code == 400

    def test_list_proxies_bridge_response(self):
        srv._zro_bridge.set("http://192.168.1.50:7070")
        bridge_resp = {
            "ok": True,
            "instruments": [{"index": 1, "name": "Klein K10-A on COM3"}],
            "selected_port": None,
        }
        with patch("httpx.get", return_value=_mock_httpx_get(bridge_resp)):
            r = client.get("/api/zro/bridge/instruments")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["instruments"] == [{"index": 1, "name": "Klein K10-A on COM3"}]

    def test_list_bridge_unreachable(self):
        srv._zro_bridge.set("http://192.168.1.50:7070")
        import httpx
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            r = client.get("/api/zro/bridge/instruments")
        assert r.status_code == 502

    def test_select_persists_and_pushes_to_bridge(self):
        srv._zro_bridge.set("http://192.168.1.50:7070")
        srv._prefs["argyll"] = {"port": "", "instrument_name": "", "correction_path": ""}
        captured = {}

        def mock_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return _mock_httpx_post({"ok": True, "argyll_port": "2"})

        with patch("httpx.post", side_effect=mock_post):
            r = client.post(
                "/api/zro/bridge/instrument",
                json={
                    "port": "2",
                    "instrument_name": "X-Rite i1 Display Pro, USB",
                    "correction_path": "/etc/argyll/i1d3.ccss",
                },
            )

        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["pushed_to_bridge"] is True
        assert d["argyll"] == {
            "port": "2",
            "instrument_name": "X-Rite i1 Display Pro, USB",
            "correction_path": "/etc/argyll/i1d3.ccss",
        }
        assert srv._prefs["argyll"] == {
            "port": "2",
            "instrument_name": "X-Rite i1 Display Pro, USB",
            "correction_path": "/etc/argyll/i1d3.ccss",
        }
        assert captured["url"] == "http://192.168.1.50:7070/config/argyll-port"
        assert captured["json"] == {"port": "2", "correction_path": "/etc/argyll/i1d3.ccss"}

    def test_select_persists_even_if_bridge_unreachable(self):
        srv._zro_bridge.set("http://192.168.1.50:7070")
        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            r = client.post("/api/zro/bridge/instrument", json={"port": "1"})

        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["pushed_to_bridge"] is False
        assert srv._prefs["argyll"]["port"] == "1"

    def test_select_persists_without_bridge_configured(self):
        srv._zro_bridge.set("")
        r = client.post("/api/zro/bridge/instrument", json={"port": "3", "instrument_name": "Meter"})
        assert r.status_code == 200
        d = r.json()
        assert d["pushed_to_bridge"] is False
        assert srv._prefs["argyll"] == {"port": "3", "instrument_name": "Meter", "correction_path": ""}


# ── POST /api/zro/trigger ──────────────────────────────────────────────────────

class TestZroTrigger:
    def test_trigger_not_configured(self):
        srv._zro_bridge.set("")
        r = client.post("/api/zro/trigger")
        assert r.status_code == 400
        assert "not configured" in r.json()["detail"].lower()

    def test_trigger_ok(self):
        srv._zro_bridge.set("http://192.168.1.50:7070")
        bridge_resp = {"ok": True, "method": "key", "key": "space", "window": "ColourSpace ZRO"}
        with patch("httpx.post", return_value=_mock_httpx_post(bridge_resp)):
            r = client.post("/api/zro/trigger")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["method"] == "key"

    def test_trigger_bridge_unreachable(self):
        srv._zro_bridge.set("http://192.168.1.50:7070")
        import httpx
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            r = client.post("/api/zro/trigger")
        assert r.status_code == 502
        assert "Cannot reach" in r.json()["detail"]

    def test_trigger_bridge_error_response(self):
        srv._zro_bridge.set("http://192.168.1.50:7070")
        from httpx import HTTPStatusError
        err = HTTPStatusError("502", request=MagicMock(), response=MagicMock(text="ZRO window not found"))
        with patch("httpx.post", side_effect=err):
            r = client.post("/api/zro/trigger")
        assert r.status_code == 502

    def test_trigger_proxies_to_correct_url(self):
        srv._zro_bridge.set("http://10.0.0.5:7070")
        bridge_resp = {"ok": True, "method": "key"}
        captured = {}
        def mock_post(url, **kwargs):
            captured["url"] = url
            return _mock_httpx_post(bridge_resp)
        with patch("httpx.post", side_effect=mock_post):
            client.post("/api/zro/trigger")
        assert captured["url"] == "http://10.0.0.5:7070/measure"


# ── Optional url parameter passthrough (#555) ───────────────────────────────

class TestBridgeUrlParamPassthrough:
    def test_status_uses_query_url_over_stored(self):
        srv._zro_bridge.set("http://stored-host:7070")
        bridge_resp = {"ok": True, "backend": "pyautogui"}
        captured = {}
        def mock_get(url, **kwargs):
            captured["url"] = url
            return _mock_httpx_get(bridge_resp)
        with patch("httpx.get", side_effect=mock_get):
            r = client.get("/api/zro/bridge/status?url=http://candidate-host:7070")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["url"] == "http://candidate-host:7070"
        assert captured["url"] == "http://candidate-host:7070/status"

    def test_status_falls_back_to_stored_when_no_url(self):
        srv._zro_bridge.set("http://stored-host:7070")
        bridge_resp = {"ok": True, "backend": "pyautogui"}
        captured = {}
        def mock_get(url, **kwargs):
            captured["url"] = url
            return _mock_httpx_get(bridge_resp)
        with patch("httpx.get", side_effect=mock_get):
            r = client.get("/api/zro/bridge/status")
        assert captured["url"] == "http://stored-host:7070/status"

    def test_measure_uses_body_url_over_stored(self):
        srv._zro_bridge.set("http://stored-host:7070")
        bridge_resp = {"ok": True, "method": "key"}
        captured = {}
        def mock_post(url, **kwargs):
            captured["url"] = url
            return _mock_httpx_post(bridge_resp)
        with patch("httpx.post", side_effect=mock_post):
            r = client.post("/api/bridge/measure", json={"url": "http://candidate-host:7070"})
        assert r.status_code == 200
        assert captured["url"] == "http://candidate-host:7070/measure"

    def test_measure_falls_back_to_stored_when_no_url(self):
        srv._zro_bridge.set("http://stored-host:7070")
        bridge_resp = {"ok": True, "method": "key"}
        captured = {}
        def mock_post(url, **kwargs):
            captured["url"] = url
            return _mock_httpx_post(bridge_resp)
        with patch("httpx.post", side_effect=mock_post):
            r = client.post("/api/bridge/measure")
        assert captured["url"] == "http://stored-host:7070/measure"

    def test_measure_no_url_configured_raises_400(self):
        srv._zro_bridge.set("")
        r = client.post("/api/bridge/measure", json={"url": None})
        assert r.status_code == 400
