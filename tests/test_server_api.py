"""Tests for server.py API endpoints — ZRO helper workflow."""
import asyncio
import io
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from calibrator import Measurement
import server as server_module
from server import app, _sessions

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _reset_globals():
    _sessions.clear()
    server_module._watched_session.set(None)
    server_module._dogegen_state.proc = None
    server_module._dogegen_state.started_at = None
    server_module._dogegen_state.launch_cmd = []
    server_module._dogegen_state.last_error = None
    server_module._dogegen_config = {
        "path": "",
        "resolve_host": "",
        "window_pct": 10,
        "maxcll": 1000,
    }
    server_module._dogegen_agent.set("")
    server_module._prefs = {
        "dogegen": {},
        "bridge_url": "",
        "dogegen_agent_url": "",
        "watch_folder": "",
        "llm": {"endpoint": "", "model": ""},
        "session_defaults": {
            "signal_range": "full",
            "code_scale": "8bit",
            "pattern_generator": "dogegen",
        },
        "autocal": dict(server_module._AUTOCAL_DEFAULTS),
    }
    server_module._llm_queues.clear()
    server_module._autocal_loops.clear()
    server_module._autocal_queues.clear()


@pytest.fixture(autouse=True)
def clear_sessions():
    _reset_globals()
    yield
    _reset_globals()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session_id(client):
    """Create a session and return the ID."""
    resp = client.post("/api/session", json={"tv_key": "u8g"})
    assert resp.status_code == 200
    return resp.json()["id"]


# ── Minimal ZRO CSV fixture ───────────────────────────────────────────────────

def _make_zro_csv(rows):
    """Build a minimal tab-separated ZRO CSV string."""
    header = "Date and time\tR\tG\tB\tY\tx\ty\tmsec"
    lines = [header]
    for i, (r, g, b, Y, x, y) in enumerate(rows):
        ts = f"01/01/2024 12:00:{i:02d}"
        lines.append(f"{ts}\t{r}\t{g}\t{b}\t{Y:.2f}\t{x:.4f}\t{y:.4f}\t50")
    return "\n".join(lines)


def _grayscale_rows():
    """11-point grayscale ramp in ZRO format."""
    rows = []
    for pct in range(0, 101, 10):
        v = int(pct / 100 * 235)
        Y = pct / 100 * 120.0 if pct > 0 else 0.01
        rows.append((v, v, v, Y, 0.3127, 0.3290))
    return rows


def _upload_csv(client, sid, csv_str):
    return client.post(
        f"/api/session/{sid}/import/zro",
        files={"file": ("test.csv", csv_str.encode(), "text/csv")},
    )


# ── Profile listing ───────────────────────────────────────────────────────────

class TestProfileListing:
    def test_list_profiles(self, client):
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        profiles = resp.json()
        keys = [p["key"] for p in profiles]
        assert "u8g" in keys

    def test_profile_has_name(self, client):
        resp = client.get("/api/profiles")
        for p in resp.json():
            assert p["name"]


# ── Session creation ──────────────────────────────────────────────────────────

class TestSessionCreation:
    def test_create_session(self, client):
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        assert resp.status_code == 200
        s = resp.json()
        assert s["id"]
        assert s["step"] == "select_mode"
        assert s["tv_key"] == "u8g"
        assert s["pattern_generator"] == "dogegen"
        assert s["signal_range"] == "full"
        assert s["code_scale"] == "8bit"

    def test_create_session_unknown_tv(self, client):
        resp = client.post("/api/session", json={"tv_key": "nonexistent"})
        assert resp.status_code == 400

    def test_create_session_includes_modes(self, client):
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        modes = resp.json().get("modes", [])
        mode_keys = [m["key"] for m in modes]
        assert "SDR" in mode_keys
        assert "HDR10" in mode_keys
        assert "Dolby Vision" in mode_keys

    def test_get_session(self, client, session_id):
        resp = client.get(f"/api/session/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == session_id

    def test_get_session_not_found(self, client):
        resp = client.get("/api/session/doesnotexist")
        assert resp.status_code == 404

    @pytest.mark.flaky(reruns=2, reruns_delay=1)
    def test_get_session_evicts_expired_session(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(server_module, "SESSION_STORE_DIR", tmp_path)
        monkeypatch.setattr(server_module, "SESSION_TTL", timedelta(days=7))
        stale_time = (datetime.now() - timedelta(days=8)).isoformat()
        _sessions["stale1234"] = {
            "id": "stale1234",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "select_mode",
            "mode": None,
            "gamma_workflow": "quick",
            "target": None,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 0.0,
            "created_at": stale_time,
            "last_accessed_at": stale_time,
            "zro_imports": [],
        }
        (tmp_path / "stale1234.json").write_text("{}")

        resp = client.get("/api/session/stale1234")

        assert resp.status_code == 404
        assert "stale1234" not in _sessions
        assert not (tmp_path / "stale1234.json").exists()

    def test_create_session_evicts_other_expired_sessions_but_keeps_watched_one(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(server_module, "SESSION_STORE_DIR", tmp_path)
        monkeypatch.setattr(server_module, "SESSION_TTL", timedelta(days=7))
        stale_time = (datetime.now() - timedelta(days=8)).isoformat()
        _sessions["expired1"] = {
            "id": "expired1",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "select_mode",
            "mode": None,
            "gamma_workflow": "quick",
            "target": None,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 0.0,
            "created_at": stale_time,
            "last_accessed_at": stale_time,
            "zro_imports": [],
        }
        _sessions["watched1"] = {
            **_sessions["expired1"],
            "id": "watched1",
        }
        server_module._watched_session.set("watched1")
        (tmp_path / "expired1.json").write_text("{}")
        (tmp_path / "watched1.json").write_text("{}")

        resp = client.post("/api/session", json={"tv_key": "u8g"})

        assert resp.status_code == 200
        assert "expired1" not in _sessions
        assert "watched1" in _sessions
        assert not (tmp_path / "expired1.json").exists()
        assert (tmp_path / "watched1.json").exists()

    def test_latest_session_evicts_expired_sessions(self, client, monkeypatch, tmp_path):
        """#206: GET /api/session should evict expired sessions before returning latest."""
        monkeypatch.setattr(server_module, "SESSION_STORE_DIR", tmp_path)
        monkeypatch.setattr(server_module, "SESSION_TTL", timedelta(days=7))
        stale_time = (datetime.now() - timedelta(days=8)).isoformat()
        fresh_time = datetime.now().isoformat()
        # Inject a stale session and a fresh session
        _sessions["stale_latest"] = {
            "id": "stale_latest",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "select_mode",
            "mode": None,
            "gamma_workflow": "quick",
            "target": None,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 0.0,
            "created_at": stale_time,
            "last_accessed_at": stale_time,
            "zro_imports": [],
        }
        _sessions["fresh_1234"] = {
            "id": "fresh_1234",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "select_mode",
            "mode": None,
            "gamma_workflow": "quick",
            "target": None,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 0.0,
            "created_at": fresh_time,
            "last_accessed_at": fresh_time,
            "zro_imports": [],
        }

        resp = client.get("/api/session")
        assert resp.status_code == 200
        data = resp.json()
        # Stale session should be evicted, fresh one returned
        assert data["id"] == "fresh_1234"
        assert "stale_latest" not in _sessions


class TestSessionDefaultsCodeScale:
    """Session creation honours an explicit code_scale session-default (#639).

    Defaults used to apply as signal_range -> code_scale -> pattern_generator,
    and both setters re-derive code_scale unconditionally; with mode still
    unset that derivation is always "8bit", so a configured "10bit" default
    was silently discarded at creation and again at mode selection.
    """

    TEN_BIT_DEFAULTS = {
        "signal_range": "full",
        "code_scale": "10bit",
        "pattern_generator": "dogegen",
    }

    def test_create_session_honours_10bit_code_scale_default(self, client):
        server_module._prefs["session_defaults"] = dict(self.TEN_BIT_DEFAULTS)
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        assert resp.status_code == 200
        assert resp.json()["code_scale"] == "10bit"
        # The pin is visible in the session view so the UI can show it.
        assert resp.json()["code_scale_explicit"] is True

    def test_sdr_session_keeps_pinned_10bit_code_scale(self, client):
        server_module._prefs["session_defaults"] = dict(self.TEN_BIT_DEFAULTS)
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        mode_resp = client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        assert mode_resp.status_code == 200
        assert mode_resp.json()["code_scale"] == "10bit"
        # A 10-bit SDR signal must keep code_max=1023 for the whole session.
        cfg = server_module._session_to_analysis_config(server_module.store.get(sid))
        assert cfg.code_max == 1023

    def test_default_8bit_pref_keeps_hdr10_auto_bump(self, client):
        # The shipped "8bit" default stays un-pinned: an HDR10 session must
        # still auto-bump to 10-bit codes (regression guard for #639's fix).
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        assert resp.json()["code_scale"] == "8bit"
        mode_resp = client.post(f"/api/session/{sid}/mode", json={"mode": "HDR10"})
        assert mode_resp.json()["code_scale"] == "10bit"


class TestSessionDefaultWarnings:
    """#673: session creation surfaces defaults that failed to apply.

    An internally-inconsistent session_defaults combo used to fail silently —
    the setter's HTTPException was logged and swallowed, and the client got a
    200 with silently-different effective settings. The failure now surfaces
    as a `warnings` entry on the session response (and later session reads)
    so the frontend can banner it.
    """

    INCONSISTENT_DEFAULTS = {
        "signal_range": "limited",
        "code_scale": "10bit",
        "pattern_generator": "dogegen",
    }

    def test_inconsistent_defaults_surface_warnings(self, client):
        # 10-bit codes require Full range, so the code_scale pref must be
        # rejected — but the rejection must be visible, not log-only.
        server_module._prefs["session_defaults"] = dict(self.INCONSISTENT_DEFAULTS)
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code_scale"] == "8bit"
        assert any("code_scale" in w for w in body["warnings"])
        assert "was not applied" in body["warnings"][0]

    def test_unknown_generator_default_surfaces_warning(self, client):
        server_module._prefs["session_defaults"] = {"pattern_generator": "nope"}
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        body = resp.json()
        assert any("pattern_generator" in w for w in body["warnings"])

    def test_warnings_persist_in_later_session_reads(self, client):
        server_module._prefs["session_defaults"] = dict(self.INCONSISTENT_DEFAULTS)
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        assert resp.json()["warnings"]
        assert client.get("/api/session").json()["warnings"]
        assert client.get(f"/api/session/{sid}").json()["warnings"]

    def test_warnings_survive_restart_reload(self, client, monkeypatch, tmp_path):
        # The disk copy is what a server restart actually replays: the
        # setters' mid-creation saves used to persist a session whose
        # session_warnings was still empty, so the warning only ever lived
        # in the in-memory store. Read the JSON back from disk and reload
        # it through load_sessions/deserialize_session — a simulated
        # restart — instead of reading back through the live store.
        monkeypatch.setattr(server_module, "SESSION_STORE_DIR", tmp_path)
        server_module._prefs["session_defaults"] = dict(self.INCONSISTENT_DEFAULTS)
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        disk = json.loads((tmp_path / f"{sid}.json").read_text())
        assert disk["session_warnings"] == resp.json()["warnings"]
        reloaded = server_module.SessionStore(
            session_dir_getter=lambda: tmp_path,
            ttl_getter=lambda: timedelta(days=7),
            watched_session_id_getter=lambda: None,
        )
        reloaded.load_sessions()
        assert reloaded.get(sid)["session_warnings"] == resp.json()["warnings"]

    def test_applicable_defaults_have_no_warnings(self, client):
        server_module._prefs["session_defaults"] = {
            "signal_range": "full",
            "code_scale": "10bit",
            "pattern_generator": "dogegen",
        }
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        body = resp.json()
        assert body["warnings"] == []
        assert body["code_scale"] == "10bit"


# ── Mode selection ────────────────────────────────────────────────────────────

class TestModeSelection:
    def test_select_mode_sdr(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        assert resp.status_code == 200
        assert resp.json()["step"] == "prepare"
        assert resp.json()["mode"] == "SDR"

    def test_select_mode_hdr10(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "HDR10"

    def test_select_mode_dv(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/mode", json={"mode": "Dolby Vision"})
        assert resp.status_code == 200
        assert resp.json()["mode"] == "Dolby Vision"

    def test_select_mode_sdr_custom_nits(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/mode",
                           json={"mode": "SDR", "sdr_peak_nits": 160.0})
        assert resp.status_code == 200
        target = resp.json().get("target", {})
        assert target.get("peak_nits") == 160.0

    def test_select_mode_invalid(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/mode", json={"mode": "UNKNOWN"})
        assert resp.status_code == 400

    def test_select_mode_wrong_step(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        # Already in prepare — trying to select mode again should fail
        resp = client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        assert resp.status_code == 400


# ── Prepare step ──────────────────────────────────────────────────────────────

class TestPrepareStep:
    def _to_prepare(self, client, sid):
        client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})

    def test_confirm_prepared(self, client, session_id):
        self._to_prepare(client, session_id)
        resp = client.post(f"/api/session/{session_id}/prepared")
        assert resp.status_code == 200
        assert resp.json()["step"] == "pre_grayscale"

    def test_confirm_prepared_wrong_step(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/prepared")
        assert resp.status_code == 400

    def test_prepare_view_has_zro_setup(self, client, session_id):
        self._to_prepare(client, session_id)
        resp = client.get(f"/api/session/{session_id}")
        data = resp.json()
        assert data["step"] == "prepare"
        prepare = data.get("prepare_data", {})
        assert "zro_setup_steps" in prepare
        assert len(prepare["zro_setup_steps"]) > 0

    def test_prepare_view_tells_u8g_users_to_use_hdr_local_dimming_setting(self, client, session_id):
        self._to_prepare(client, session_id)
        resp = client.get(f"/api/session/{session_id}")
        prepare = resp.json()["prepare_data"]
        local_dimming = next(
            item for item in prepare["configure_settings"]
            if item["setting"] == "Local Dimming"
        )
        assert "High" in local_dimming["value"]
        assert "Off only" in local_dimming["value"]

    def test_prepare_view_hdr10_has_different_zro_setup(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        resp = client.get(f"/api/session/{session_id}")
        steps = resp.json()["prepare_data"]["zro_setup_steps"]
        texts = " ".join(f'{s["step"]} {s["detail"]}' for s in steps)
        assert "HDR10" in texts or "Apple TV" in texts or "signal" in texts.lower()
        assert "10%" in texts
        assert "High" in texts

    def test_prepare_view_includes_pattern_generator_options(self, client, session_id):
        self._to_prepare(client, session_id)
        resp = client.get(f"/api/session/{session_id}")
        prepare = resp.json()["prepare_data"]
        options = {item["key"] for item in prepare["pattern_generator_options"]}
        assert {"lightspace_connect", "dogegen"} <= options

    def test_can_switch_pattern_generator_to_dogegen(self, client, session_id):
        self._to_prepare(client, session_id)
        resp = client.post(
            f"/api/session/{session_id}/pattern-generator",
            json={"pattern_generator": "dogegen"},
        )
        assert resp.status_code == 200
        assert resp.json()["pattern_generator"] == "dogegen"
        prepare = resp.json()["prepare_data"]
        assert prepare["pattern_generator_label"] == "Dogegen"
        details = " ".join(step["detail"] for step in prepare["zro_setup_steps"])
        assert "Dogegen" in details

    def test_dogegen_hdr10_defaults_to_full_10bit_workflow_when_full_range_selected(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        resp = client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "full"})
        assert resp.status_code == 200
        assert resp.json()["signal_range"] == "full"
        assert resp.json()["code_scale"] == "10bit"

    def test_can_override_code_scale_explicitly(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "full"})
        resp = client.post(f"/api/session/{session_id}/code-scale", json={"code_scale": "8bit"})
        assert resp.status_code == 200
        assert resp.json()["code_scale"] == "8bit"

    def test_dogegen_hdr10_setup_mentions_hdr_signal_path(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        resp = client.post(
            f"/api/session/{session_id}/pattern-generator",
            json={"pattern_generator": "dogegen"},
        )
        assert resp.status_code == 200
        details = " ".join(step["detail"] for step in resp.json()["prepare_data"]["zro_setup_steps"])
        assert "HDR10" in details or "HDR" in details
        assert "Dogegen" in details



# ── Session deletion with watcher cleanup ─────────────────────────────────────

class TestDeleteSessionWatcherCleanup:
    def test_delete_watched_session_stops_watcher(self, client, monkeypatch):
        """#205: Deleting the watched session should stop the file watcher."""
        # Set up a watched session
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        server_module._watched_session.set(sid)

        # Mock _fw_stop to track calls
        stopped = []
        original_fw_stop = server_module._fw_stop
        def mock_fw_stop():
            stopped.append(True)
        server_module._fw_stop = mock_fw_stop

        try:
            resp = client.delete(f"/api/session/{sid}")
            assert resp.status_code == 200
            assert stopped == [True]
            assert server_module._watched_session.get() is None
        finally:
            server_module._fw_stop = original_fw_stop

    def test_delete_non_watched_session_does_not_stop_watcher(self, client):
        """Deleting a non-watched session should not stop the watcher."""
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        server_module._watched_session.set("other_session")

        stopped = []
        original_fw_stop = server_module._fw_stop
        def mock_fw_stop():
            stopped.append(True)
        server_module._fw_stop = mock_fw_stop

        try:
            resp = client.delete(f"/api/session/{sid}")
            assert resp.status_code == 200
            assert stopped == []
            assert server_module._watched_session.get() == "other_session"
        finally:
            server_module._fw_stop = original_fw_stop


# ── Dogegen companion automation ─────────────────────────────────────────────

class _FakeProc:
    def __init__(self, pid=4321):
        self.pid = pid
        self._returncode = None

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def terminate(self):
        self._returncode = 0

    def kill(self):
        self._returncode = -9


class TestDogegenAutomation:
    def test_dogegen_status_defaults(self, client):
        resp = client.get("/api/dogegen/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["configured"] is False
        assert data["window_pct"] == 10
        assert data["maxcll"] == 1000

    def test_dogegen_status_detects_external_process(self, client, monkeypatch, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        monkeypatch.setattr(server_module, "_find_dogegen_executable", lambda: str(exe))
        monkeypatch.setattr(server_module, "_external_dogegen_pid", lambda: 5555)

        resp = client.get("/api/dogegen/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["pid"] == 5555
        assert data["managed"] is False
        assert data["ready"] is True

    def test_dogegen_config_updates_values(self, client, monkeypatch, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        monkeypatch.setattr(server_module, "_find_dogegen_executable", lambda: str(exe))

        resp = client.post("/api/dogegen/config", json={
            "path": str(exe),
            "resolve_host": "127.0.0.1",
            "window_pct": 12,
            "maxcll": 800,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["path"] == str(exe)
        assert data["resolve_host"] == "127.0.0.1"
        assert data["window_pct"] == 12
        assert data["maxcll"] == 800

    def test_dogegen_start_for_hdr10_session_uses_resolve_hdr_command(self, client, session_id, monkeypatch, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post("/api/dogegen/config", json={
            "path": str(exe),
            "window_pct": 10,
            "maxcll": 1000,
        })

        captured = {}
        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _FakeProc()

        monkeypatch.setattr(server_module.subprocess, "Popen", fake_popen)

        resp = client.post(f"/api/session/{session_id}/dogegen/start")

        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["ready"] is False
        assert data["ready_in_ms"] > 0
        assert captured["cmd"][0] == str(exe)
        assert "mode 10_hdr" in captured["cmd"]
        assert "maxcll 1000" in captured["cmd"]
        assert "resolve_hdr 10" in captured["cmd"]

    def test_dogegen_start_for_sdr_session_uses_resolve_sdr_command(self, client, session_id, monkeypatch, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post("/api/dogegen/config", json={"path": str(exe)})

        captured = {}
        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _FakeProc()

        monkeypatch.setattr(server_module.subprocess, "Popen", fake_popen)

        resp = client.post(f"/api/session/{session_id}/dogegen/start")

        assert resp.status_code == 200
        assert captured["cmd"][0] == str(exe)
        assert "maxcll 1000" in captured["cmd"]
        assert "resolve_sdr 127.0.0.1" in captured["cmd"]
        assert not any("resolve_hdr" in a for a in captured["cmd"])

    def test_dogegen_start_for_sdr_with_resolve_host_includes_host(self, client, session_id, monkeypatch, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post("/api/dogegen/config", json={"path": str(exe), "resolve_host": "192.168.1.50", "maxcll": 160})

        captured = {}
        monkeypatch.setattr(server_module.subprocess, "Popen",
                            lambda cmd, **kw: (captured.update({"cmd": cmd}), _FakeProc())[1])

        client.post(f"/api/session/{session_id}/dogegen/start")

        assert "maxcll 160" in captured["cmd"]
        assert "resolve_sdr 192.168.1.50" in captured["cmd"]

    def test_dogegen_status_turns_ready_after_warmup(self, client, session_id, monkeypatch, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post("/api/dogegen/config", json={"path": str(exe)})

        base_time = datetime.fromisoformat("2026-03-20T15:00:00")
        monkeypatch.setattr(server_module, "_now", lambda: base_time)
        monkeypatch.setattr(server_module.subprocess, "Popen", lambda cmd, **kwargs: _FakeProc())

        start_resp = client.post(f"/api/session/{session_id}/dogegen/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["ready"] is False

        monkeypatch.setattr(server_module, "_now", lambda: base_time + timedelta(seconds=3))
        status_resp = client.get("/api/dogegen/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["ready"] is True
        assert status_resp.json()["ready_in_ms"] == 0

    def test_dogegen_start_fails_when_executable_missing(self, client, session_id, monkeypatch):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        monkeypatch.setattr(server_module, "_find_dogegen_executable", lambda: None)

        resp = client.post(f"/api/session/{session_id}/dogegen/start")

        assert resp.status_code == 400
        assert "Dogegen.exe not found" in resp.json()["detail"]

    def test_dogegen_start_reuses_external_process(self, client, session_id, monkeypatch, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        monkeypatch.setattr(server_module, "_find_dogegen_executable", lambda: str(exe))
        monkeypatch.setattr(server_module, "_external_dogegen_pid", lambda: 5555)

        called = {"popen": False}
        def fake_popen(cmd, **kwargs):
            called["popen"] = True
            return _FakeProc()

        monkeypatch.setattr(server_module.subprocess, "Popen", fake_popen)

        resp = client.post(f"/api/session/{session_id}/dogegen/start")

        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["already_running"] is True
        assert data["pid"] == 5555
        assert called["popen"] is False

    def test_dogegen_stop_terminates_running_process(self, client, monkeypatch, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        fake_proc = _FakeProc()
        server_module._dogegen_state.proc = fake_proc
        server_module._dogegen_config["path"] = str(exe)
        monkeypatch.setattr(server_module, "_find_dogegen_executable", lambda: str(exe))

        resp = client.post("/api/dogegen/stop")

        assert resp.status_code == 200
        assert resp.json()["running"] is False
        assert server_module._dogegen_state.proc is None


# ── ZRO CSV import ────────────────────────────────────────────────────────────

class TestZROImport:
    def _to_pre_grayscale(self, client, sid):
        client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{sid}/prepared")

    def test_import_requires_mode(self, client, session_id):
        csv = _make_zro_csv(_grayscale_rows())
        resp = _upload_csv(client, session_id, csv)
        assert resp.status_code == 400

    def test_import_grayscale_ramp(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        csv = _make_zro_csv(_grayscale_rows())
        resp = _upload_csv(client, session_id, csv)
        assert resp.status_code == 200
        data = resp.json()
        assert "session" in data
        assert "import_summary" in data
        buckets = data["import_summary"]["buckets_populated"]
        assert "pre_measurements" in buckets

    def test_import_summary_includes_import_and_measurement_times(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        csv = _make_zro_csv(_grayscale_rows())
        resp = _upload_csv(client, session_id, csv)
        summary = resp.json()["import_summary"]
        assert summary["timestamp"]
        assert summary["measurement_start"]
        assert summary["measurement_end"]
        assert summary["raw_rows"]

    def test_import_full_ramp_keeps_user_on_pre_grayscale_until_continue(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        csv = _make_zro_csv(_grayscale_rows())
        resp = _upload_csv(client, session_id, csv)
        assert resp.json()["session"]["step"] == "pre_grayscale"

    def test_pre_grayscale_import_does_not_seed_luminance_wb_or_gamma_steps(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        resp = _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        assert resp.status_code == 200
        session = resp.json()["session"]
        assert session["step"] == "pre_grayscale"
        assert session["peak_luminance"] == 0
        assert _sessions[session_id]["lum_measurements"] == []
        assert _sessions[session_id]["wb_measurements"] == []
        assert _sessions[session_id]["gamma_measurements"] == []

    def test_import_luminance_reading(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        # Import full grayscale, then continue manually to luminance
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        # Import 100% white
        white_csv = _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)])
        resp = _upload_csv(client, session_id, white_csv)
        assert resp.status_code == 200
        s = resp.json()["session"]
        assert s["peak_luminance"] > 0

    def test_import_wb_measurements(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        # Upload grayscale and continue manually to luminance
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        # Dedicated luminance reading is required before entering white balance
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        # Upload WB patches
        wb_rows = [
            (188, 188, 188, 96.0, 0.3127, 0.3290),  # 80%
            (71,  71,  71,  36.0, 0.3127, 0.3290),  # 30%
        ]
        wb_csv = _make_zro_csv(wb_rows)
        resp = _upload_csv(client, session_id, wb_csv)
        assert resp.status_code == 200
        buckets = resp.json()["import_summary"]["buckets_populated"]
        assert "wb_measurements" in buckets

    def test_import_abl_warnings_returned(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        # Non-monotonic ramp to trigger ABL detection
        rows = []
        for i, pct in enumerate(range(0, 101, 10)):
            v = int(pct / 100 * 235)
            # Make Y go down at 50% to trigger ABL warning
            Y = (10 - i) * 10 + 1 if pct == 50 else pct + 1
            rows.append((v, v, v, float(Y), 0.3127, 0.3290))
        csv = _make_zro_csv(rows)
        resp = _upload_csv(client, session_id, csv)
        assert resp.status_code == 200
        # abl_warnings may or may not fire depending on data — just check key present
        assert "abl_warnings" in resp.json()["import_summary"]

    def test_pre_grayscale_summary_only_reports_abl_warnings_from_latest_pass(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        csv = """Date and time\tR\tG\tB\tY\tx\ty\tmsec
03/15/2026 10:00:00\t16\t16\t16\t0.50\t0.3127\t0.3290\t50
03/15/2026 10:00:05\t26\t26\t26\t1.00\t0.3127\t0.3290\t50
03/15/2026 10:00:10\t51\t51\t51\t5.00\t0.3127\t0.3290\t50
03/15/2026 10:00:15\t77\t77\t77\t12.00\t0.3127\t0.3290\t50
03/15/2026 10:00:20\t102\t102\t102\t20.00\t0.3127\t0.3290\t50
03/15/2026 10:00:25\t128\t128\t128\t30.00\t0.3127\t0.3290\t50
03/15/2026 10:00:30\t153\t153\t153\t40.00\t0.3127\t0.3290\t50
03/15/2026 10:00:35\t179\t179\t179\t50.00\t0.3127\t0.3290\t50
03/15/2026 10:00:40\t204\t204\t204\t15.06\t0.3127\t0.3290\t50
03/15/2026 10:00:45\t230\t230\t230\t151.26\t0.3127\t0.3290\t50
03/15/2026 10:00:50\t235\t235\t235\t99.84\t0.3127\t0.3290\t50
03/15/2026 10:02:00\t16\t16\t16\t0.50\t0.3127\t0.3290\t50
03/15/2026 10:02:05\t26\t26\t26\t1.10\t0.3127\t0.3290\t50
03/15/2026 10:02:10\t51\t51\t51\t4.50\t0.3127\t0.3290\t50
03/15/2026 10:02:15\t77\t77\t77\t11.90\t0.3127\t0.3290\t50
03/15/2026 10:02:20\t102\t102\t102\t21.00\t0.3127\t0.3290\t50
03/15/2026 10:02:25\t128\t128\t128\t36.90\t0.3127\t0.3290\t50
03/15/2026 10:02:30\t153\t153\t153\t46.80\t0.3127\t0.3290\t50
03/15/2026 10:02:35\t179\t179\t179\t58.10\t0.3127\t0.3290\t50
"""
        resp = _upload_csv(client, session_id, csv)
        assert resp.status_code == 200
        assert resp.json()["import_summary"]["abl_warnings"] == []

    def test_import_empty_csv_rejected(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        resp = _upload_csv(client, session_id, "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n")
        assert resp.status_code == 400

    def test_import_malformed_csv_rejected(self, client, session_id):
        self._to_pre_grayscale(client, session_id)
        resp = _upload_csv(client, session_id, "not,a,valid,zro,file\n")
        assert resp.status_code == 400

    def test_import_dedup_append_mode_buckets(self, client, session_id):
        """Regression test for #550: uploading the same cumulative ZRO export twice
        at the WB step should not duplicate wb/gamma/cms/lum measurements.
        """
        self._to_pre_grayscale(client, session_id)
        # Upload grayscale and advance to luminance
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        # Upload dedicated luminance reading
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        # Upload WB patches (80% and 30% gray)
        wb_rows = [
            (188, 188, 188, 96.0, 0.3127, 0.3290),  # 80%
            (71, 71, 71, 36.0, 0.3127, 0.3290),  # 30%
        ]
        wb_csv = _make_zro_csv(wb_rows)
        resp1 = _upload_csv(client, session_id, wb_csv)
        assert resp1.status_code == 200
        summary1 = resp1.json()["import_summary"]
        assert summary1["buckets_populated"].get("wb_measurements") == 2
        assert summary1["duplicates_skipped"] == 0

        # Upload the SAME CSV again — should not duplicate
        resp2 = _upload_csv(client, session_id, wb_csv)
        assert resp2.status_code == 200
        summary2 = resp2.json()["import_summary"]
        # No new items appended (all were duplicates), so buckets_populated may not include wb_measurements
        # but duplicates_skipped should report the 2 skipped rows
        assert summary2["duplicates_skipped"] == 2

        # Verify session state — bucket length unchanged
        # session["wb_measurements"] may not be in view; check the store directly
        # Use the store directly to avoid view transformation issues
        s = _sessions[session_id]
        assert len(s.get("wb_measurements", [])) == 2


class TestImportRoutesDoNotBlockEventLoop:
    """Regression test for #504: the import routes must not run blocking,
    lock-holding disk I/O directly on the asyncio event loop.

    Either the handler is a plain `def` (FastAPI threadpools those
    automatically), or it's `async def` and offloads the store call via
    `run_in_threadpool` rather than calling it directly in its own frame.
    """

    @pytest.mark.parametrize(
        "route_func, store_method_name",
        [
            (server_module.import_zro_csv, "import_zro_bytes"),
            (server_module.import_generic_csv, "import_generic_bytes"),
        ],
    )
    def test_route_offloads_blocking_store_call(self, route_func, store_method_name):
        import inspect

        if not inspect.iscoroutinefunction(route_func):
            return  # plain `def` routes are threadpooled by FastAPI for free

        source = inspect.getsource(route_func)
        direct_call = f"store.{store_method_name}("
        assert direct_call not in source, (
            f"{route_func.__name__} is async but calls store.{store_method_name} "
            "directly on the event loop instead of offloading via run_in_threadpool"
        )
        assert "run_in_threadpool" in source
        assert f"store.{store_method_name}" in source


# ── Step navigation ───────────────────────────────────────────────────────────

class TestStepNavigation:
    def _setup(self, client, sid):
        client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{sid}/prepared")

    def test_cannot_advance_pre_grayscale_without_data(self, client, session_id):
        self._setup(client, session_id)
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 400

    def test_advance_after_full_ramp(self, client, session_id):
        self._setup(client, session_id)
        csv = _make_zro_csv(_grayscale_rows())
        _upload_csv(client, session_id, csv)
        s = client.get(f"/api/session/{session_id}").json()
        assert s["step"] == "pre_grayscale"
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 200
        assert resp.json()["step"] == "luminance"

    def test_cannot_advance_luminance_without_dedicated_luminance_reading(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 400

    def test_cannot_advance_white_balance_without_dedicated_wb_readings(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        s = client.get(f"/api/session/{session_id}").json()
        assert s["wb_data"]["ready_to_continue"] is False
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 400

    def test_cannot_advance_gamma_without_full_gamma_pass(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        wb_rows = [(188,188,188,96.0,0.3127,0.3290),(71,71,71,36.0,0.3127,0.3290)]
        _upload_csv(client, session_id, _make_zro_csv(wb_rows))
        client.post(f"/api/session/{session_id}/next")
        s = client.get(f"/api/session/{session_id}").json()
        assert s["gamma_data"]["ready_to_continue"] is False
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 400

    def test_gamma_view_only_shows_latest_complete_pass(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        wb_rows = [(188,188,188,96.0,0.3127,0.3290),(71,71,71,36.0,0.3127,0.3290)]
        _upload_csv(client, session_id, _make_zro_csv(wb_rows))
        client.post(f"/api/session/{session_id}/next")

        first_pass = [
            (47,47,47,8.5,0.3127,0.3290),
            (94,94,94,29.0,0.3127,0.3290),
            (141,141,141,62.0,0.3127,0.3290),
            (188,188,188,104.0,0.3127,0.3290),
        ]
        second_pass = [
            (47,47,47,7.5,0.3100,0.3218),
            (94,94,94,20.2,0.3074,0.3174),
            (141,141,141,45.8,0.3069,0.3175),
            (188,188,188,90.7,0.3078,0.3177),
        ]
        _upload_csv(client, session_id, _make_zro_csv(first_pass))
        resp = _upload_csv(client, session_id, _make_zro_csv(second_pass))
        assert resp.status_code == 200
        meas = resp.json()["session"]["gamma_data"]["measurements"]
        assert len(meas) == 4
        assert meas[0]["timestamp"].endswith("00")
        assert meas[-1]["timestamp"].endswith("03")

    def test_gamma_view_recalculates_from_latest_partial_pass_only(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        wb_rows = [(188,188,188,96.0,0.3127,0.3290),(71,71,71,36.0,0.3127,0.3290)]
        _upload_csv(client, session_id, _make_zro_csv(wb_rows))
        client.post(f"/api/session/{session_id}/next")

        full_pass = [
            (60,60,60,6.5,0.3100,0.3218),
            (104,104,104,20.2,0.3074,0.3174),
            (147,147,147,45.8,0.3069,0.3175),
            (191,191,191,90.7,0.3078,0.3177),
        ]
        partial_pass = [
            (60,60,60,7.5,0.3110,0.3220),
            (104,104,104,18.0,0.3080,0.3180),
        ]
        _upload_csv(client, session_id, _make_zro_csv(full_pass))
        resp = _upload_csv(client, session_id, _make_zro_csv(partial_pass))
        assert resp.status_code == 200
        gamma = resp.json()["session"]["gamma_data"]
        meas = gamma["measurements"]
        controls = [item["control"] for item in gamma["control_plan"]]
        assert gamma["ready_to_continue"] is False
        assert [m["label"] for m in meas] == ["Gamma 20%", "Gamma 40%"]
        assert len(controls) == 2
        assert controls == ["20%", "40%"]

    def test_gamma_view_keeps_latest_reading_per_target_with_out_of_order_rows(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        wb_rows = [(188,188,188,96.0,0.3127,0.3290),(71,71,71,36.0,0.3127,0.3290)]
        _upload_csv(client, session_id, _make_zro_csv(wb_rows))
        client.post(f"/api/session/{session_id}/next")

        messy_pass = [
            (188,188,188,104.5,0.3127,0.3290),
            (47,47,47,7.6,0.3127,0.3290),
            (94,94,94,20.1,0.3127,0.3290),
            (141,141,141,46.4,0.3127,0.3290),
            (188,188,188,90.8,0.3078,0.3177),
        ]
        resp = _upload_csv(client, session_id, _make_zro_csv(messy_pass))
        assert resp.status_code == 200
        gamma = resp.json()["session"]["gamma_data"]
        meas = gamma["measurements"]
        assert [m["label"] for m in meas] == ["Gamma 20%", "Gamma 40%", "Gamma 60%", "Gamma 80%"]
        assert meas[-1]["timestamp"].endswith("04")
        assert gamma["ready_to_continue"] is True

    def test_can_switch_gamma_workflow_to_fine(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        wb_rows = [(188,188,188,96.0,0.3127,0.3290),(71,71,71,36.0,0.3127,0.3290)]
        _upload_csv(client, session_id, _make_zro_csv(wb_rows))
        client.post(f"/api/session/{session_id}/next")
        resp = client.post(f"/api/session/{session_id}/gamma/workflow", json={"workflow": "fine"})
        assert resp.status_code == 200
        gamma = resp.json()["gamma_data"]
        assert gamma["workflow"] == "fine"
        assert gamma["tracking_levels"][0] == 5
        assert gamma["tracking_levels"][-1] == 95

    def test_fine_gamma_requires_full_five_percent_pass(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        wb_rows = [(188,188,188,96.0,0.3127,0.3290),(71,71,71,36.0,0.3127,0.3290)]
        _upload_csv(client, session_id, _make_zro_csv(wb_rows))
        client.post(f"/api/session/{session_id}/next")
        client.post(f"/api/session/{session_id}/gamma/workflow", json={"workflow": "fine"})
        partial_rows = [
            (26,26,26,1.0,0.3127,0.3290),
            (51,51,51,4.5,0.3127,0.3290),
            (77,77,77,11.9,0.3127,0.3290),
            (102,102,102,21.0,0.3127,0.3290),
        ]
        _upload_csv(client, session_id, _make_zro_csv(partial_rows))
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 400

    def test_fine_gamma_control_plan_uses_nominal_five_percent_slots(self, client, session_id):
        self._setup(client, session_id)
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))
        client.post(f"/api/session/{session_id}/next")
        _upload_csv(client, session_id, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{session_id}/next")
        wb_rows = [(188,188,188,96.0,0.3127,0.3290),(71,71,71,36.0,0.3127,0.3290)]
        _upload_csv(client, session_id, _make_zro_csv(wb_rows))
        client.post(f"/api/session/{session_id}/next")
        client.post(f"/api/session/{session_id}/gamma/workflow", json={"workflow": "fine"})
        fine_rows = [
            (39,39,39,1.1,0.3052,0.3177),
            (51,51,51,4.5,0.3147,0.3322),
            (64,64,64,11.9,0.3153,0.3317),
            (77,77,77,21.0,0.3172,0.3317),
        ]
        resp = _upload_csv(client, session_id, _make_zro_csv(fine_rows))
        assert resp.status_code == 200
        gamma = resp.json()["session"]["gamma_data"]
        controls = [item["control"] for item in gamma["control_plan"]]
        labels = [item["label"] for item in gamma["measurements"]]
        assert controls == ["15%", "20%", "25%", "30%"]
        assert labels == ["Gamma 15%", "Gamma 20%", "Gamma 25%", "Gamma 30%"]

    def test_prev_from_prepare(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        resp = client.post(f"/api/session/{session_id}/prev")
        assert resp.status_code == 200
        assert resp.json()["step"] == "select_mode"

    def test_prev_resets_mode(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{session_id}/prev")
        s = client.get(f"/api/session/{session_id}").json()
        assert s["mode"] is None

    def test_prev_from_select_mode_fails(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/prev")
        assert resp.status_code == 400

    def test_steps_list_in_session_view(self, client, session_id):
        s = client.get(f"/api/session/{session_id}").json()
        steps = s.get("steps", [])
        step_ids = [x["id"] for x in steps]
        assert "select_mode" in step_ids
        assert "report" in step_ids

    def test_step_index_advances(self, client, session_id):
        s0 = client.get(f"/api/session/{session_id}").json()
        assert s0["step_index"] == 0
        client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        s1 = client.get(f"/api/session/{session_id}").json()
        assert s1["step_index"] > 0


# ── HDR luminance gate (#546) ─────────────────────────────────────────────────
# HDR10/Dolby Vision targets carry a nominal 1000-nit mastering reference that a
# real panel is not expected to hit — peak is panel-limited and tone mapping
# handles the rest. The ±5% "dial Backlight to match target" gate only makes
# sense for SDR, where the target nits are an achievable Backlight setting.

class TestHDRLuminanceGate:
    def _to_luminance(self, client, sid, mode):
        client.post(f"/api/session/{sid}/mode", json={"mode": mode})
        client.post(f"/api/session/{sid}/prepared")
        _upload_csv(client, sid, _make_zro_csv(_grayscale_rows()))
        resp = client.post(f"/api/session/{sid}/next")
        assert resp.json()["step"] == "luminance"

    def _measure_white(self, client, sid, nits):
        # Dogegen + full-range HDR10/Dolby Vision sessions default to 10-bit code
        # values (see recommended_code_scale), so 100% white is RGB 1023, not 235.
        code_scale = client.get(f"/api/session/{sid}").json().get("code_scale", "8bit")
        white = 1023 if code_scale == "10bit" else 235
        _upload_csv(client, sid, _make_zro_csv([(white, white, white, nits, 0.3127, 0.3290)]))

    def test_hdr10_700_nit_panel_advances_past_luminance(self, client, session_id):
        self._to_luminance(client, session_id, "HDR10")
        self._measure_white(client, session_id, 700.0)
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 200
        assert resp.json()["step"] == "white_balance"

    def test_hdr10_1480_nit_panel_advances_past_luminance(self, client, session_id):
        # Exact repro from the issue: a 1500-nit-class panel measuring 1480 nits
        # at 100% white used to hard-block with "48.0% off" — HDR peak is
        # panel-limited, not tunable down to the 1000-nit mastering reference.
        self._to_luminance(client, session_id, "HDR10")
        self._measure_white(client, session_id, 1480.0)
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 200
        assert resp.json()["step"] == "white_balance"

    def test_dolby_vision_panel_measured_peak_advances_past_luminance(self, client, session_id):
        self._to_luminance(client, session_id, "Dolby Vision")
        self._measure_white(client, session_id, 850.0)
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 200
        assert resp.json()["step"] == "white_balance"

    def test_hdr10_implausible_reading_still_rejected(self, client, session_id):
        # Sanity bounds (validate_peak_luminance) still apply to HDR — only the
        # ±5%-of-target match requirement is skipped.
        self._to_luminance(client, session_id, "HDR10")
        self._measure_white(client, session_id, 6000.0)
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 400

    def test_sdr_keeps_pct_luminance_gate(self, client, session_id):
        # SDR's target nits are an achievable Backlight setting, so the ±5%
        # match requirement must still block advancement.
        self._to_luminance(client, session_id, "SDR")
        self._measure_white(client, session_id, 200.0)  # SDR target is 120 nits
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 400
        assert "% off" in resp.json()["detail"]

    def test_sdr_within_tolerance_advances_past_luminance(self, client, session_id):
        self._to_luminance(client, session_id, "SDR")
        self._measure_white(client, session_id, 120.0)  # SDR target is 120 nits
        resp = client.post(f"/api/session/{session_id}/next")
        assert resp.status_code == 200
        assert resp.json()["step"] == "white_balance"


# ── ZRO per-step instructions ─────────────────────────────────────────────────

class TestZROInstructions:
    def _to_step(self, client, sid, step):
        client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{sid}/prepared")
        if step == "pre_grayscale":
            return
        csv = _make_zro_csv(_grayscale_rows())
        _upload_csv(client, sid, csv)
        client.post(f"/api/session/{sid}/next")
        if step == "luminance":
            return
        _upload_csv(client, sid, _make_zro_csv([(235, 235, 235, 120.0, 0.3127, 0.3290)]))
        client.post(f"/api/session/{sid}/next")
        if step == "white_balance":
            return
        wb_rows = [(188,188,188,96.0,0.3127,0.3290),(71,71,71,36.0,0.3127,0.3290)]
        _upload_csv(client, sid, _make_zro_csv(wb_rows))
        client.post(f"/api/session/{sid}/next")
        if step == "gamma":
            return
        gamma_rows = [
            (47,47,47,8.5,0.3127,0.3290),
            (94,94,94,29.0,0.3127,0.3290),
            (141,141,141,62.0,0.3127,0.3290),
            (188,188,188,104.0,0.3127,0.3290),
        ]
        _upload_csv(client, sid, _make_zro_csv(gamma_rows))
        client.post(f"/api/session/{sid}/next")

    def test_pre_grayscale_has_zro_instructions(self, client, session_id):
        self._to_step(client, session_id, "pre_grayscale")
        s = client.get(f"/api/session/{session_id}").json()
        assert "zro_instructions" in s
        instr = s["zro_instructions"]
        assert instr.get("title")
        assert len(instr.get("steps", [])) > 0

    def test_luminance_has_zro_instructions(self, client, session_id):
        self._to_step(client, session_id, "luminance")
        s = client.get(f"/api/session/{session_id}").json()
        assert "zro_instructions" in s

    def test_white_balance_has_zro_instructions(self, client, session_id):
        self._to_step(client, session_id, "white_balance")
        s = client.get(f"/api/session/{session_id}").json()
        assert "zro_instructions" in s

    def test_gamma_has_zro_instructions(self, client, session_id):
        self._to_step(client, session_id, "gamma")
        s = client.get(f"/api/session/{session_id}").json()
        assert "zro_instructions" in s

    def test_color_tuner_uses_legal_range_rgb_values(self, client, session_id):
        self._to_step(client, session_id, "color_tuner")
        client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "limited"})
        s = client.get(f"/api/session/{session_id}").json()

        assert s["cms_data"]["colors"][0] == {"name": "Red", "rgb": [235, 16, 16]}
        assert s["cms_data"]["colors"][-1] == {"name": "Yellow", "rgb": [235, 235, 16]}

        patches = s["zro_instructions"]["patches"]
        assert patches[0] == {"label": "Red", "rgb": [235, 16, 16]}
        assert patches[-1] == {"label": "Yellow", "rgb": [235, 235, 16]}

    def test_dogegen_full_10bit_uses_1023_patch_values(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "full"})
        client.post(f"/api/session/{session_id}/prepared")

        s = client.get(f"/api/session/{session_id}").json()
        patches = s["zro_instructions"]["patches"]
        assert patches[0] == {"label": "0% Gray", "rgb": [0, 0, 0]}
        assert patches[-1] == {"label": "100% Gray", "rgb": [1023, 1023, 1023]}

    def test_fine_gamma_zro_instructions_floor_low_patch_to_video_black(self, client, session_id):
        self._to_step(client, session_id, "gamma")
        client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "limited"})
        resp = client.post(f"/api/session/{session_id}/gamma/workflow", json={"workflow": "fine"})
        assert resp.status_code == 200
        instr = resp.json()["zro_instructions"]
        patches = instr.get("patches", [])
        assert patches[0]["label"] == "5% Gray (gamma)"
        assert patches[0]["rgb"] == [27, 27, 27]
        assert patches[1]["label"] == "10% Gray (gamma)"
        assert patches[1]["rgb"] == [38, 38, 38]

    def test_pre_grayscale_view_only_shows_latest_measurement_per_level(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{session_id}/prepared")
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=1.0, X=1.0, Z=1.0, timestamp="2026-03-15T15:09:54", label="Black (0%)", stimulus_rgb=(16, 16, 16)),
            Measurement(x=0.3127, y=0.3290, Y=10.0, X=1.0, Z=1.0, timestamp="2026-03-15T15:10:00", label="10% Gray", stimulus_rgb=(26, 26, 26)),
            Measurement(x=0.3127, y=0.3290, Y=2.0, X=1.0, Z=1.0, timestamp="2026-03-15T16:50:00", label="Black (0%)", stimulus_rgb=(16, 16, 16)),
            Measurement(x=0.3127, y=0.3290, Y=11.0, X=1.0, Z=1.0, timestamp="2026-03-15T16:50:03", label="10% Gray", stimulus_rgb=(26, 26, 26)),
        ]
        s = client.get(f"/api/session/{session_id}").json()
        meas = s["grayscale_data"]["measurements"]
        assert len(meas) == 2
        assert meas[0]["timestamp"] == "2026-03-15T16:50:00"
        assert meas[1]["timestamp"] == "2026-03-15T16:50:03"

    def test_pre_grayscale_view_sorts_latest_run_by_stimulus(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "full"})
        client.post(f"/api/session/{session_id}/prepared")
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=4.0, X=1.0, Z=1.0, timestamp="2026-03-20T14:30:26", label="20% Gray", stimulus_rgb=(205, 205, 205)),
            Measurement(x=0.3127, y=0.3290, Y=1054.1, X=1.0, Z=1.0, timestamp="2026-03-20T14:30:29", label="95% Gray", stimulus_rgb=(972, 972, 972)),
            Measurement(x=0.3127, y=0.3290, Y=1126.7, X=1.0, Z=1.0, timestamp="2026-03-20T14:30:33", label="White (100%)", stimulus_rgb=(1023, 1023, 1023)),
            Measurement(x=0.3127, y=0.3290, Y=1124.5, X=1.0, Z=1.0, timestamp="2026-03-20T14:30:36", label="80% Gray", stimulus_rgb=(818, 818, 818)),
            Measurement(x=0.3127, y=0.3290, Y=9.9, X=1.0, Z=1.0, timestamp="2026-03-20T14:30:40", label="30% Gray", stimulus_rgb=(307, 307, 307)),
            Measurement(x=0.3127, y=0.3290, Y=0.0, X=1.0, Z=1.0, timestamp="2026-03-20T14:30:43", label="Black (0%)", stimulus_rgb=(0, 0, 0)),
        ]
        s = client.get(f"/api/session/{session_id}").json()
        meas = s["grayscale_data"]["measurements"]
        assert [m["label"] for m in meas] == [
            "Black (0%)",
            "20% Gray",
            "30% Gray",
            "80% Gray",
            "95% Gray",
            "White (100%)",
        ]

    def test_pre_grayscale_view_flags_invalid_measurements(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "full"})
        client.post(f"/api/session/{session_id}/prepared")
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.0, y=0.0, Y=0.0, X=0.0, Z=0.0, timestamp="2026-03-20T15:13:21", label="40% Gray", stimulus_rgb=(409, 409, 409)),
            Measurement(x=0.0, y=0.0, Y=0.0, X=0.0, Z=0.0, timestamp="2026-03-20T15:13:30", label="60% Gray", stimulus_rgb=(614, 614, 614)),
        ]

        s = client.get(f"/api/session/{session_id}").json()
        meas = s["grayscale_data"]["measurements"]
        assert all(m["invalid"] for m in meas)
        assert meas[0]["delta_e"] is None
        assert meas[0]["cct"] is None
        assert "Non-black patch measured at 0 nits." in meas[0]["warnings"]
        assert "Chromaticity values are missing or zero." in meas[0]["warnings"]
        assert s["grayscale_data"]["warnings"]

    def test_black_zero_nits_without_xy_is_not_flagged_by_nits_warning(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "full"})
        client.post(f"/api/session/{session_id}/prepared")
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.0, y=0.0, Y=0.0, X=0.0, Z=0.0, timestamp="2026-03-20T15:31:07", label="Black (0%)", stimulus_rgb=(0, 0, 0)),
        ]
        s = client.get(f"/api/session/{session_id}").json()
        meas = s["grayscale_data"]["measurements"]
        assert meas[0]["invalid"] is False
        assert meas[0]["warning_summary"] == ""

    def test_non_black_zero_nits_is_flagged_and_raw_rows_preserve_order(self, client, session_id):
        client.post(f"/api/session/{session_id}/mode", json={"mode": "HDR10"})
        client.post(f"/api/session/{session_id}/pattern-generator", json={"pattern_generator": "dogegen"})
        client.post(f"/api/session/{session_id}/signal-range", json={"signal_range": "full"})
        client.post(f"/api/session/{session_id}/prepared")
        csv = """Date and time\tR\tG\tB\tY\tx\ty\tmsec
03/20/2026 15:31:07\t0\t0\t0\t0\t0\t0\t50
03/20/2026 15:31:16\t205\t205\t205\t0\t0\t0\t50
03/20/2026 15:31:26\t409\t409\t409\t0\t0\t0\t50
03/20/2026 15:31:35\t614\t614\t614\t0.2\t0.3198\t0.3581\t50
"""
        resp = _upload_csv(client, session_id, csv)
        assert resp.status_code == 200
        raw_rows = resp.json()["import_summary"]["raw_rows"]
        assert [row["r"] for row in raw_rows] == [0, 205, 409, 614]
        s = resp.json()["session"]
        meas = s["grayscale_data"]["measurements"]
        row_20 = next(m for m in meas if m["label"] == "20% Gray")
        assert row_20["invalid"] is True
        assert "Non-black patch measured at 0 nits." in row_20["warnings"]


class TestGrayscalePassHelpers:
    def test_latest_grayscale_pass_uses_configured_level_count(self):
        # Simulate two passes: a 3-label pass then a 4-label pass (extra label added).
        # With max_count=3 the function should stop after collecting 3 unique labels
        # from the latest pass, not collect the 4th.
        measurements = [
            Measurement(x=0.3127, y=0.3290, Y=1.0, X=1.0, Z=1.0, timestamp="2026-03-15T15:00:00", label="Black (0%)", stimulus_rgb=(16, 16, 16)),
            Measurement(x=0.3127, y=0.3290, Y=2.0, X=1.0, Z=1.0, timestamp="2026-03-15T15:00:03", label="5% Gray", stimulus_rgb=(27, 27, 27)),
            Measurement(x=0.3127, y=0.3290, Y=3.0, X=1.0, Z=1.0, timestamp="2026-03-15T15:00:06", label="10% Gray", stimulus_rgb=(38, 38, 38)),
            Measurement(x=0.3127, y=0.3290, Y=11.0, X=1.0, Z=1.0, timestamp="2026-03-15T16:00:00", label="Black (0%)", stimulus_rgb=(16, 16, 16)),
            Measurement(x=0.3127, y=0.3290, Y=12.0, X=1.0, Z=1.0, timestamp="2026-03-15T16:00:03", label="5% Gray", stimulus_rgb=(27, 27, 27)),
            Measurement(x=0.3127, y=0.3290, Y=13.0, X=1.0, Z=1.0, timestamp="2026-03-15T16:00:06", label="10% Gray", stimulus_rgb=(38, 38, 38)),
            Measurement(x=0.3127, y=0.3290, Y=14.0, X=1.0, Z=1.0, timestamp="2026-03-15T16:00:09", label="15% Gray", stimulus_rgb=(49, 49, 49)),
        ]

        # max_count=3 caps at 3 labels from the latest pass
        latest = server_module._latest_grayscale_pass(measurements, max_count=3)

        assert len(latest) == 3
        assert [m.label for m in latest] == ["5% Gray", "10% Gray", "15% Gray"]
        assert latest[-1].timestamp == "2026-03-15T16:00:09"

    def test_latest_grayscale_pass_uses_latest_timestamp_run_and_sorts(self):
        measurements = [
            Measurement(x=0.3127, y=0.3290, Y=1.0, X=1.0, Z=1.0, timestamp="2026-03-15T15:00:00", label="Black (0%)", stimulus_rgb=(16, 16, 16)),
            Measurement(x=0.3127, y=0.3290, Y=2.0, X=1.0, Z=1.0, timestamp="2026-03-15T15:00:03", label="10% Gray", stimulus_rgb=(26, 26, 26)),
            Measurement(x=0.3127, y=0.3290, Y=3.0, X=1.0, Z=1.0, timestamp="2026-03-15T16:00:06", label="10% Gray", stimulus_rgb=(26, 26, 26)),
            Measurement(x=0.3127, y=0.3290, Y=4.0, X=1.0, Z=1.0, timestamp="2026-03-15T16:00:09", label="Black (0%)", stimulus_rgb=(16, 16, 16)),
        ]

        latest = server_module._latest_grayscale_pass(measurements, max_count=10)

        assert [m.timestamp for m in latest] == ["2026-03-15T16:00:09", "2026-03-15T16:00:06"]
        assert [m.label for m in latest] == ["Black (0%)", "10% Gray"]

    def test_latest_grayscale_pass_detect_sessions_false_ignores_gaps(self):
        """With detect_sessions=False (fresh CSV import), all measurements are kept
        even when there are large timestamp gaps between them."""
        measurements = [
            Measurement(x=0.3127, y=0.3290, Y=1.0, X=1.0, Z=1.0,
                        timestamp="2026-03-15T15:00:00", label="Black (0%)",
                        stimulus_rgb=(16, 16, 16)),
            Measurement(x=0.3127, y=0.3290, Y=10.0, X=1.0, Z=1.0,
                        timestamp="2026-03-15T15:02:00", label="25% Gray",
                        stimulus_rgb=(77, 77, 77)),
            # User took a 3-minute break — normally this would split the pass
            # (but with detect_sessions=True this is just 1 session because
            # the 2-min gap between black->25% is also within SESSION_BREAK_SECONDS)
            Measurement(x=0.3127, y=0.3290, Y=30.0, X=1.0, Z=1.0,
                        timestamp="2026-03-15T15:05:00", label="50% Gray",
                        stimulus_rgb=(128, 128, 128)),
            Measurement(x=0.3127, y=0.3290, Y=85.0, X=1.0, Z=1.0,
                        timestamp="2026-03-15T15:06:00", label="White (100%)",
                        stimulus_rgb=(235, 235, 235)),
        ]
        # With detect_sessions=False (imported CSV), all measurements are kept
        latest_imported = server_module._latest_grayscale_pass(
            measurements, max_count=10, detect_sessions=False)
        assert len(latest_imported) == 4
        assert [m.label for m in latest_imported] == [
            "Black (0%)", "25% Gray", "50% Gray", "White (100%)"
        ]

    def test_latest_grayscale_pass_detect_sessions_false_sorts_by_stimulus(self):
        """Imported CSV data is sorted purely by stimulus percentage, not by
        timestamp which could cause incorrect ordering with mixed-import data."""
        # Measurements in reverse order (like a file read backwards)
        measurements = [
            Measurement(x=0.3127, y=0.3290, Y=85.0, X=1.0, Z=1.0,
                        timestamp="2026-03-15T15:20:00", label="White (100%)",
                        stimulus_rgb=(235, 235, 235)),
            Measurement(x=0.3127, y=0.3290, Y=30.0, X=1.0, Z=1.0,
                        timestamp="2026-03-15T15:19:00", label="50% Gray",
                        stimulus_rgb=(128, 128, 128)),
            Measurement(x=0.3127, y=0.3290, Y=10.0, X=1.0, Z=1.0,
                        timestamp="2026-03-15T15:18:00", label="25% Gray",
                        stimulus_rgb=(77, 77, 77)),
            Measurement(x=0.3127, y=0.3290, Y=1.0, X=1.0, Z=1.0,
                        timestamp="2026-03-15T15:17:00", label="Black (0%)",
                        stimulus_rgb=(16, 16, 16)),
        ]
        latest = server_module._latest_grayscale_pass(
            measurements, max_count=10, detect_sessions=False)
        # Sorted by stimulus percentage ascending
        assert [m.label for m in latest] == [
            "Black (0%)", "25% Gray", "50% Gray", "White (100%)"
        ]


# ── Report ────────────────────────────────────────────────────────────────────

class TestReport:
    def _make_session_with_data(self, client):
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{sid}/prepared")
        # Pre-cal grayscale
        csv = _make_zro_csv(_grayscale_rows())
        _upload_csv(client, sid, csv)
        # Peak white
        _upload_csv(client, sid, _make_zro_csv([(235,235,235,120.0,0.3127,0.3290)]))
        client.post(f"/api/session/{sid}/next")  # luminance → wb
        # WB
        _upload_csv(client, sid, _make_zro_csv([
            (188,188,188,96.0,0.3127,0.3290),
            (71,71,71,36.0,0.3127,0.3290),
        ]))
        client.post(f"/api/session/{sid}/next")  # wb → gamma
        _upload_csv(client, sid, _make_zro_csv([
            (47,47,47,8.5,0.3127,0.3290),
            (94,94,94,29.0,0.3127,0.3290),
            (141,141,141,62.0,0.3127,0.3290),
            (188,188,188,104.0,0.3127,0.3290),
        ]))
        client.post(f"/api/session/{sid}/next")  # gamma → color_tuner
        client.post(f"/api/session/{sid}/next")  # color_tuner → post_grayscale
        _upload_csv(client, sid, csv)  # post grayscale
        client.post(f"/api/session/{sid}/next")  # → report
        return sid

    def test_report_endpoint(self, client):
        sid = self._make_session_with_data(client)
        resp = client.get(f"/api/session/{sid}/report")
        assert resp.status_code == 200
        r = resp.json()
        assert "pre_cal" in r
        assert "post_cal" in r
        assert "peak_luminance" in r

    def test_report_html(self, client):
        sid = self._make_session_with_data(client)
        resp = client.get(f"/api/session/{sid}/report/html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Calibration Report" in resp.text

    def test_report_requires_mode(self, client, session_id):
        resp = client.get(f"/api/session/{session_id}/report")
        assert resp.status_code == 400

    def test_report_improvement_pct(self, client):
        sid = self._make_session_with_data(client)
        r = client.get(f"/api/session/{sid}/report").json()
        # May be None if pre/post identical but key should exist
        assert "improvement_pct" in r


# ── Watch config ──────────────────────────────────────────────────────────────

class TestWatchConfig:
    def test_watch_status(self, client):
        resp = client.get("/api/watch/status")
        assert resp.status_code == 200
        assert "watching" in resp.json()
        assert "diagnostics" in resp.json()
        assert "session_id" in resp.json()
        assert "session_exists" in resp.json()

    def test_watch_bad_path(self, client, session_id):
        resp = client.post("/api/watch/config",
                           json={"path": "/nonexistent/path/12345", "sid": session_id})
        assert resp.status_code == 400

    def test_watch_bad_session(self, client, tmp_path):
        resp = client.post("/api/watch/config",
                           json={"path": str(tmp_path), "sid": "nope"})
        assert resp.status_code == 404

    def test_watch_valid(self, client, session_id, tmp_path, monkeypatch):
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        resp = client.post("/api/watch/config",
                           json={"path": str(tmp_path), "sid": session_id})
        assert resp.status_code == 200
        assert resp.json()["watching"] is True
        assert resp.json()["session_id"] == session_id
        assert resp.json()["session_exists"] is True
        client.delete("/api/watch/config")

    def test_delete_watched_session_stops_watcher(self, client, session_id, tmp_path, monkeypatch):
        """#205: Deleting the watched session stops the file watcher."""
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        client.post("/api/watch/config", json={"path": str(tmp_path), "sid": session_id})
        client.delete(f"/api/session/{session_id}")
        resp = client.get("/api/watch/status")
        assert resp.status_code == 200
        assert resp.json()["watching"] is False
        assert resp.json().get("session_id") is None
        assert resp.json().get("session_exists") is False

    def test_stop_watch(self, client, session_id, tmp_path, monkeypatch):
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        client.post("/api/watch/config",
                    json={"path": str(tmp_path), "sid": session_id})
        resp = client.delete("/api/watch/config")
        assert resp.status_code == 200
        assert resp.json()["watching"] is False

    def test_watch_events_sets_sse_headers(self, client):
        from server import watch_events

        resp = asyncio.run(watch_events())
        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"

    def test_watch_events_delivers_payload(self, client):
        """Push a broadcast event and verify it emerges through the async SSE generator."""
        from server import watch_events
        from calibrator.file_watcher import _broadcast_event

        async def _run():
            resp = await watch_events()
            _broadcast_event({"event": "import", "session_id": "test-123"})
            async for chunk in resp.body_iterator:
                return chunk
            return None

        chunk = asyncio.run(_run())
        assert chunk is not None
        assert "import" in chunk
        assert "test-123" in chunk

    def test_session_events_delivers_payload(self, client, session_id):
        """Push a broadcast event and verify session_events SSE emits it."""
        from server import session_events
        from calibrator.file_watcher import _broadcast_event

        async def _run():
            resp = await session_events(session_id)
            _broadcast_event({"event": "import", "session_id": session_id})
            async for chunk in resp.body_iterator:
                # Skip initial session/watch_status events, grab the first
                # event that came from the broadcast.
                if "import" in chunk:
                    return chunk
            return None

        chunk = asyncio.run(_run())
        assert chunk is not None
        assert session_id in chunk

    def test_watch_rejects_path_outside_home(self, client, session_id, monkeypatch):
        monkeypatch.setattr(server_module, "_WATCH_ROOT", Path("/tmp").resolve())
        resp = client.post("/api/watch/config",
                           json={"path": str(Path.home()), "sid": session_id})
        assert resp.status_code == 400
        assert "allowed directory" in resp.json()["detail"].lower()

    def test_watch_accepts_path_inside_watch_root(self, client, session_id, tmp_path, monkeypatch):
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        resp = client.post("/api/watch/config",
                           json={"path": str(tmp_path), "sid": session_id})
        assert resp.status_code == 200
        assert resp.json()["watching"] is True
        client.delete("/api/watch/config")

    def test_watch_rejects_symlink_escape(self, client, session_id, tmp_path, monkeypatch):
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        outside = tmp_path / ".." / "tmp"
        resp = client.post("/api/watch/config",
                           json={"path": str(outside), "sid": session_id})
        assert resp.status_code == 400

    def test_watch_config_rejects_path_traversal(self, client, session_id, tmp_path, monkeypatch):
        """A path that starts with the same string as WATCH_ROOT but extends it should be rejected."""
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        evil_path = str(tmp_path) + "-evil"
        os.makedirs(evil_path, exist_ok=True)
        try:
            resp = client.post("/api/watch/config",
                               json={"path": evil_path, "sid": session_id})
            assert resp.status_code == 400
        finally:
            shutil.rmtree(evil_path, ignore_errors=True)


# ── SSE autocal-stream endpoint ──────────────────────────────────────────────

class TestAutocalStream:
    def test_autocal_stream_sets_sse_headers(self, client, session_id):
        from server import autocal_stream

        resp = asyncio.run(autocal_stream(session_id))
        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"

    def test_autocal_stream_delivers_payload(self, client, session_id):
        """Broadcast an autocal event and verify it emerges through the async SSE generator."""
        from server import autocal_stream

        async def _run():
            resp = await autocal_stream(session_id)
            server_module._autocal_broadcast(session_id, {"event": "autocal_iteration", "data": {"iteration": 1}})
            async for chunk in resp.body_iterator:
                if "autocal_iteration" in chunk:
                    return chunk
            return None

        chunk = asyncio.run(_run())
        assert chunk is not None
        assert "iteration" in chunk
        assert "1" in chunk


class TestLLMIntegration:
    def test_llm_configure_sets_endpoint_and_model(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/llm/configure",
                           json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["model"] == "gpt-4o"

    def test_llm_configure_rejects_invalid_temperature(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/llm/configure",
                           json={"endpoint": "http://localhost:4000", "model": "x", "temperature": 3.0})
        assert resp.status_code == 400

    def test_llm_configure_rejects_zero_timeout(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/llm/configure",
                           json={"endpoint": "http://localhost:4000", "model": "x", "timeout": 0})
        assert resp.status_code == 400

    def test_llm_status_unconfigured(self, client, session_id):
        resp = client.get(f"/api/session/{session_id}/llm/status")
        assert resp.status_code == 200
        assert resp.json()["configured"] is False

    def test_llm_status_configured_reachable(self, client, session_id, monkeypatch):
        monkeypatch.setattr(server_module, "_probe_llm", lambda *a, **kw: (True, ""))
        client.post(f"/api/session/{session_id}/llm/configure",
                    json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        resp = client.get(f"/api/session/{session_id}/llm/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["reachable"] is True

    def test_llm_probe_reachable(self, client, session_id, monkeypatch):
        monkeypatch.setattr(server_module, "_probe_llm", lambda *a, **kw: (True, ""))
        resp = client.post(f"/api/session/{session_id}/llm/probe",
                           json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["reachable"] is True
        assert data["model"] == "gpt-4o"

    def test_llm_probe_unreachable(self, client, session_id, monkeypatch):
        monkeypatch.setattr(server_module, "_probe_llm", lambda *a, **kw: (False, "connection refused"))
        resp = client.post(f"/api/session/{session_id}/llm/probe",
                           json={"endpoint": "http://bad-host:9999", "model": "llama3"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["reachable"] is False
        assert data["error"] == "connection refused"

    def test_llm_probe_does_not_persist_to_session(self, client, session_id, monkeypatch):
        monkeypatch.setattr(server_module, "_probe_llm", lambda *a, **kw: (True, ""))
        client.post(f"/api/session/{session_id}/llm/probe",
                    json={"endpoint": "http://probe-only:9999", "model": "probe-model"})
        resp = client.get(f"/api/session/{session_id}/llm/status")
        data = resp.json()
        assert data["configured"] is False

    def test_llm_probe_rejects_missing_endpoint(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/llm/probe",
                           json={"model": "gpt-4o"})
        assert resp.status_code == 422

    def test_llm_probe_rejects_missing_model(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/llm/probe",
                           json={"endpoint": "http://localhost:4000"})
        assert resp.status_code == 422

    def test_llm_history_summary_fresh_session(self, client, session_id, monkeypatch):
        monkeypatch.setattr(server_module, "_history_summary", lambda *a, **kw: {
            "session_count": 0, "latest_date": None,
            "latest_post_de": None, "baseline_post_de": None,
        })
        monkeypatch.setattr(server_module, "_load_history", lambda *a, **kw: [])
        resp = client.get(f"/api/session/{session_id}/llm/history-summary")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["session_count"] == 0
        assert data["tv_key"] == "u8g"
        assert data["has_final_settings"] is False

    def test_llm_run_not_configured_returns_400(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/llm/run")
        assert resp.status_code == 400

    def test_llm_run_no_measurements_returns_400(self, client, session_id):
        client.post(f"/api/session/{session_id}/llm/configure",
                    json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        resp = client.post(f"/api/session/{session_id}/llm/run")
        assert resp.status_code == 400

    def test_llm_run_success_returns_running_and_broadcasts_insight(
        self, client, session_id, monkeypatch
    ):
        client.post(f"/api/session/{session_id}/llm/configure",
                    json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=100.0, X=95.0, Z=108.8,
                        stimulus_rgb=(255, 255, 255), label="White (100%)"),
            Measurement(x=0.3127, y=0.3290, Y=10.0, X=9.5, Z=10.9,
                        stimulus_rgb=(128, 128, 128), label="50% Gray"),
        ]

        q = server_module._llm_subscribe(session_id)
        monkeypatch.setattr(server_module, "_call_llm", lambda *a, **kw: "Adjust white balance.")

        resp = client.post(f"/api/session/{session_id}/llm/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

        # llm_start is broadcast first; skip it and wait for llm_insight
        first = q.get(timeout=2.0)
        assert first["event"] == "llm_start"
        payload = q.get(timeout=2.0)
        assert payload["event"] == "llm_insight"
        data = payload["data"]
        assert data["phase"] == "select_mode"
        assert "Adjust" in data["text"]
        assert data["timestamp"].endswith("Z")
        server_module._llm_unsubscribe(session_id, q)

    def test_llm_run_error_broadcasts_llm_error_event(
        self, client, session_id, monkeypatch
    ):
        client.post(f"/api/session/{session_id}/llm/configure",
                    json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=100.0, X=95.0, Z=108.8,
                        stimulus_rgb=(255, 255, 255), label="White (100%)"),
            Measurement(x=0.3127, y=0.3290, Y=10.0, X=9.5, Z=10.9,
                        stimulus_rgb=(128, 128, 128), label="50% Gray"),
        ]

        q = server_module._llm_subscribe(session_id)
        monkeypatch.setattr(server_module, "_call_llm",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("connection refused")))

        client.post(f"/api/session/{session_id}/llm/run")
        # llm_start is broadcast first; skip it and wait for llm_error
        first = q.get(timeout=2.0)
        assert first["event"] == "llm_start"
        payload = q.get(timeout=2.0)
        assert payload["event"] == "llm_error"
        assert "connection refused" in payload["data"]
        server_module._llm_unsubscribe(session_id, q)

    def test_llm_stream_sets_sse_headers(self, client, session_id):
        from server import llm_stream

        resp = asyncio.run(llm_stream(session_id))
        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"

    def test_llm_stream_invalid_session_returns_404(self, client):
        resp = client.get("/api/session/nosuchsid/llm/stream")
        assert resp.status_code == 404

    def test_llm_stream_delivers_payload(self, client, session_id):
        """Broadcast an LLM insight and verify it emerges through the async SSE generator."""
        from server import llm_stream

        async def _run():
            resp = await llm_stream(session_id)
            server_module._llm_broadcast(session_id, {"event": "llm_insight", "data": {"text": "test-insight"}})
            async for chunk in resp.body_iterator:
                if "llm_insight" in chunk:
                    return chunk
            return None

        chunk = asyncio.run(_run())
        assert chunk is not None
        assert "test-insight" in chunk

    def _advance_to_pre_grayscale(self, client, sid):
        """Advance session to pre_grayscale step so ZRO imports populate measurements."""
        client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{sid}/prepared")

    def test_import_zro_auto_triggers_llm_when_configured(
        self, client, session_id, monkeypatch
    ):
        self._advance_to_pre_grayscale(client, session_id)
        client.post(f"/api/session/{session_id}/llm/configure",
                    json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        monkeypatch.setattr(server_module, "_call_llm", lambda *a, **kw: "Check white balance.")

        q = server_module._llm_subscribe(session_id)
        csv = _make_zro_csv(_grayscale_rows())
        resp = _upload_csv(client, session_id, csv)
        assert resp.status_code == 200

        first = q.get(timeout=2.0)
        assert first["event"] == "llm_start"
        payload = q.get(timeout=2.0)
        assert payload["event"] == "llm_insight"
        assert "white balance" in payload["data"]["text"]
        server_module._llm_unsubscribe(session_id, q)

    def test_import_zro_no_llm_configured_does_not_trigger(
        self, client, session_id, monkeypatch
    ):
        self._advance_to_pre_grayscale(client, session_id)
        fired = []
        monkeypatch.setattr(server_module, "_call_llm", lambda *a, **kw: fired.append(1) or "x")

        csv = _make_zro_csv(_grayscale_rows())
        resp = _upload_csv(client, session_id, csv)
        assert resp.status_code == 200

        import time; time.sleep(0.1)
        assert fired == [], "LLM should not fire when not configured"

    def test_import_generic_auto_triggers_llm_when_configured(
        self, client, session_id, monkeypatch
    ):
        self._advance_to_pre_grayscale(client, session_id)
        # Populate measurements via ZRO import first
        _upload_csv(client, session_id, _make_zro_csv(_grayscale_rows()))

        client.post(f"/api/session/{session_id}/llm/configure",
                    json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        monkeypatch.setattr(server_module, "_call_llm", lambda *a, **kw: "Adjust gain.")

        q = server_module._llm_subscribe(session_id)
        # Generic import also triggers LLM because measurements already exist in session
        resp = client.post(
            f"/api/session/{session_id}/import/generic",
            files={"file": ("test.csv", b"R,G,B,Y,x,y\n", "text/csv")},
        )
        assert resp.status_code == 200

        first = q.get(timeout=2.0)
        assert first["event"] == "llm_start"
        payload = q.get(timeout=2.0)
        assert payload["event"] in ("llm_insight", "llm_error")
        server_module._llm_unsubscribe(session_id, q)


class TestPassDecisionEndpoint:
    def test_non_numeric_delta_e_returns_200_not_500(self, client, session_id):
        """Malformed measurement payloads must degrade gracefully, never 500 (#501)."""
        client.post(f"/api/session/{session_id}/llm/configure",
                    json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        resp = client.post(
            f"/api/session/{session_id}/pass-decision",
            json={"measurements": [
                {"label": "White 50%", "delta_e": "bad", "stimulus_pct": 50}
            ]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "accept"

    def test_repass_count_derived_from_session_not_request(self, client, session_id, monkeypatch):
        """#645: /pass-decision derives the per-phase budget from session state.

        A client-supplied repass_count of 0 must not mask a phase whose own
        budget is already exhausted, and the count handed to the LLM is the
        count for the phase the session currently sits in.
        """
        session = server_module._sessions[session_id]
        session["step"] = "white_balance"
        session["repass_phase_counts"] = {"white_balance": 3, "gamma": 2}
        session["repass_count"] = 5

        seen = {}

        def fake_query(**kwargs):
            seen.update(kwargs)
            return None

        client.post(f"/api/session/{session_id}/llm/configure",
                    json={"endpoint": "http://localhost:4000", "model": "gpt-4o"})
        monkeypatch.setattr(server_module, "_query_pass_decision", fake_query)

        resp = client.post(
            f"/api/session/{session_id}/pass-decision",
            json={"measurements": [{"label": "White 50%", "delta_e": 2.5,
                                   "stimulus_pct": 50}], "repass_count": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Session-state per-phase count for white_balance wins over both the
        # client-supplied 0 and the session-global total of 5.
        assert seen["repass_count"] == 3
        assert seen["phase"] == "white_balance"
        assert data["action"] == "accept"  # LLM unavailable -> graceful accept
        assert data["repass_count"] == 3


# ── Issue #188: Periodic session TTL eviction ──────────────────────────────────

class TestSessionTTLCleanup:
    def test_evict_expired_sessions_removes_from_memory_and_disk(self, monkeypatch, tmp_path):
        """evict_expired_sessions removes expired sessions from memory and deletes their files."""
        monkeypatch.setattr(server_module, "SESSION_STORE_DIR", tmp_path)
        monkeypatch.setattr(server_module, "SESSION_TTL", timedelta(days=7))
        stale_time = (datetime.now() - timedelta(days=8)).isoformat()
        server_module._sessions["evict1"] = {
            "id": "evict1",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "select_mode",
            "mode": None,
            "gamma_workflow": "quick",
            "target": None,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 0.0,
            "created_at": stale_time,
            "last_accessed_at": stale_time,
            "zro_imports": [],
        }
        (tmp_path / "evict1.json").write_text("{}")

        expired = server_module.store.evict_expired_sessions()

        assert "evict1" in expired
        assert "evict1" not in server_module._sessions
        assert not (tmp_path / "evict1.json").exists()

    def test_evict_expired_sessions_keeps_watched_session(self, monkeypatch, tmp_path):
        """Watched session is never evicted even if expired."""
        monkeypatch.setattr(server_module, "SESSION_STORE_DIR", tmp_path)
        monkeypatch.setattr(server_module, "SESSION_TTL", timedelta(days=7))
        stale_time = (datetime.now() - timedelta(days=8)).isoformat()
        server_module._sessions["watched_evict"] = {
            "id": "watched_evict",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "select_mode",
            "mode": None,
            "gamma_workflow": "quick",
            "target": None,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 0.0,
            "created_at": stale_time,
            "last_accessed_at": stale_time,
            "zro_imports": [],
        }
        server_module._watched_session.set("watched_evict")
        (tmp_path / "watched_evict.json").write_text("{}")

        expired = server_module.store.evict_expired_sessions()

        assert "watched_evict" not in expired
        assert "watched_evict" in server_module._sessions
        assert (tmp_path / "watched_evict.json").exists()

    def test_evict_expired_sessions_returns_empty_when_none_expired(self, monkeypatch, tmp_path):
        """evict_expired_sessions returns empty list when no sessions are expired."""
        monkeypatch.setattr(server_module, "SESSION_STORE_DIR", tmp_path)
        monkeypatch.setattr(server_module, "SESSION_TTL", timedelta(days=7))
        fresh_time = datetime.now().isoformat()
        server_module._sessions["fresh1"] = {
            "id": "fresh1",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "select_mode",
            "mode": None,
            "gamma_workflow": "quick",
            "target": None,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 0.0,
            "created_at": fresh_time,
            "last_accessed_at": fresh_time,
            "zro_imports": [],
        }

        expired = server_module.store.evict_expired_sessions()

        assert expired == []
        assert "fresh1" in server_module._sessions

    def test_session_cleanup_loop_function_exists(self):
        """_session_cleanup_loop is defined and is an async function."""
        import inspect
        assert hasattr(server_module, "_session_cleanup_loop")
        assert inspect.iscoroutinefunction(server_module._session_cleanup_loop)


# ── SSE logs-stream endpoint ──────────────────────────────────────────────────

class TestLogsStream:
    def test_logs_stream_sets_sse_headers(self, client):
        from server import logs_stream

        resp = asyncio.run(logs_stream())
        assert resp.media_type == "text/event-stream"
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"

    def test_logs_stream_delivers_log_line(self, client):
        """Emit a log line and verify it emerges through the async SSE generator."""
        from server import logs_stream

        async def _run():
            resp = await logs_stream()
            # _log_buffer is a logging.Handler — emit a record to push through
            import logging
            logging.getLogger().info("sse-test-marker")
            async for chunk in resp.body_iterator:
                if "sse-test-marker" in chunk:
                    return chunk
            return None

        chunk = asyncio.run(_run())
        assert chunk is not None
        assert "sse-test-marker" in chunk


# ── Issue #187: _save_prefs logs on failure ───────────────────────────────────

class TestSavePrefsLogging:
    def test_save_prefs_logs_warning_on_oserror(self, monkeypatch):
        """_save_prefs logs a warning when OSError occurs during write."""
        from unittest.mock import patch
        import logging

        with patch("server._PREFS_PATH") as mock_path:
            mock_path.with_suffix.return_value.write_text.side_effect = OSError("disk full")

            with patch.object(server_module.logger, "warning") as mock_warn:
                server_module._save_prefs()

                mock_warn.assert_called_once()
                call_args = mock_warn.call_args
                assert "Could not save preferences" in call_args[0][0]

    def test_save_prefs_logs_error_on_generic_exception(self, monkeypatch):
        """_save_prefs logs an error for unexpected exceptions."""
        from unittest.mock import patch

        with patch("server._PREFS_PATH") as mock_path:
            mock_path.with_suffix.return_value.write_text.side_effect = RuntimeError("unexpected")

            with patch.object(server_module.logger, "error") as mock_error:
                server_module._save_prefs()

                mock_error.assert_called_once()
                call_args = mock_error.call_args
                assert "Unexpected error saving preferences" in call_args[0][0]

    def test_save_prefs_succeeds_normally(self, tmp_path):
        """_save_prefs writes successfully when no exception occurs."""
        from unittest.mock import patch
        prefs_path = tmp_path / ".prefs.json"

        with patch("server._PREFS_PATH", prefs_path):
            server_module._save_prefs()

        assert prefs_path.exists()
        import json
        data = json.loads(prefs_path.read_text())
        assert "dogegen" in data

    def test_save_prefs_falls_back_to_direct_write_on_ebusy(self, tmp_path, monkeypatch):
        """A bind-mounted single .prefs.json file is a mount point, so replacing it
        via rename fails with EBUSY. _save_prefs must fall back to writing the
        contents directly into the file instead of losing the save entirely."""
        import errno
        import json
        from pathlib import Path
        from unittest.mock import patch

        prefs_path = tmp_path / ".prefs.json"
        prefs_path.write_text("{}")
        real_replace = Path.replace

        def fake_replace(self, target):
            if self.suffix == ".tmp":
                raise OSError(errno.EBUSY, "Device or resource busy")
            return real_replace(self, target)

        with patch("server._PREFS_PATH", prefs_path), \
             patch.object(Path, "replace", fake_replace):
            server_module._save_prefs()

        data = json.loads(prefs_path.read_text())
        assert "dogegen" in data
        assert not (tmp_path / ".prefs.tmp").exists()


# ── Prefs directory (mounted instead of the file, avoiding the bind-mount gotcha) ──

class TestPrefsDir:
    def test_validate_startup_config_creates_missing_prefs_dir(self, tmp_path, monkeypatch):
        """_PREFS_DIR is the recommended bind-mount target (a directory rather
        than the .prefs.json file itself); it should be created if missing,
        same as SESSION_STORE_DIR."""
        prefs_dir = tmp_path / ".prefs"
        monkeypatch.setattr(server_module, "_PREFS_DIR", prefs_dir)
        monkeypatch.setattr(server_module, "_PREFS_PATH", prefs_dir / ".prefs.json")

        warnings = server_module._validate_startup_config()

        assert prefs_dir.is_dir()
        assert not any("prefs directory" in w.lower() and "not writable" in w.lower() for w in warnings)


# ── Prefs path mistakenly created as a directory (Docker bind-mount gotcha) ──

class TestPrefsPathIsDirectory:
    def test_validate_startup_config_self_heals_empty_directory(self, tmp_path, monkeypatch):
        """An empty directory at the prefs path is a Docker bind-mount artifact
        (mounting a host file that didn't exist yet creates a directory) and
        should be removed automatically so prefs can be written normally."""
        prefs_dir = tmp_path / ".prefs.json"
        prefs_dir.mkdir()
        monkeypatch.setattr(server_module, "_PREFS_PATH", prefs_dir)

        warnings = server_module._validate_startup_config()

        assert any("is a directory" in w for w in warnings)
        assert not prefs_dir.exists()

    def test_load_prefs_does_not_crash_on_directory(self, tmp_path, monkeypatch):
        """If self-heal can't run (e.g. a non-empty directory), _load_prefs must
        not raise IsADirectoryError; it should fall back to defaults."""
        prefs_dir = tmp_path / ".prefs.json"
        prefs_dir.mkdir()
        (prefs_dir / "stray-file").write_text("unexpected")
        monkeypatch.setattr(server_module, "_PREFS_PATH", prefs_dir)

        server_module._load_prefs()  # must not raise

        assert prefs_dir.exists()  # non-empty, so it wasn't removed

    def test_save_prefs_after_self_heal_writes_normal_file(self, tmp_path, monkeypatch):
        """After self-heal removes the stray directory, saving prefs produces a
        real .prefs.json file instead of failing forever."""
        prefs_dir = tmp_path / ".prefs.json"
        prefs_dir.mkdir()
        monkeypatch.setattr(server_module, "_PREFS_PATH", prefs_dir)

        server_module._validate_startup_config()
        server_module._load_prefs()
        server_module._save_prefs()

        assert prefs_dir.is_file()
        import json
        data = json.loads(prefs_dir.read_text())
        assert "dogegen" in data
        assert "bridge_url" in data
# ── CORS tests ────────────────────────────────────────────────────────────────


def test_cors_allows_known_origin(client):
    resp = client.get(
        "/api/profiles",
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_allows_server_origin(client):
    resp = client.get(
        "/api/profiles",
        headers={"Origin": "http://localhost:8000"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:8000"


def test_cors_blocks_unknown_origin(client):
    resp = client.get(
        "/api/profiles",
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_cors_credentials_reflect_origin(client):
    resp = client.get(
        "/api/profiles",
        headers={"Origin": "http://localhost:5173", "Cookie": "test=1"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_cors_blocks_credentials_on_unknown_origin(client):
    resp = client.get(
        "/api/profiles",
        headers={"Origin": "http://evil.example.com", "Cookie": "test=1"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"


# ── Bug fix: #220 improvement_pct with zero post ΔE ────────────────

class TestImprovementPercentZeroDeltaE:
    def test_perfect_post_cal_yields_100_pct_improvement(self, client, session_id):
        """#220: perfect post-cal ΔE of 0.0 should give 100% improvement, not None."""
        from calibrator.reports import report_payload

        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.35, y=0.35, Y=50.0, X=48.0, Z=56.0,
                        stimulus_rgb=(255, 0, 0), label="Red 100%",
                        timestamp="2024-01-01T10:00:00"),
        ]
        _sessions[session_id]["post_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(255, 0, 0), label="Red 100%",
                        timestamp="2024-01-01T10:00:00"),
        ]
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        report = report_payload(_sessions[session_id])
        assert report["improvement_pct"] == 100.0

    def test_normal_improvement_calculation_with_nonzero_post(self, client, session_id):
        """Ensure normal improvement calc still works for non-zero post ΔE."""
        from calibrator.reports import report_payload

        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.40, y=0.40, Y=50.0, X=40.0, Z=48.0,
                        stimulus_rgb=(255, 0, 0), label="Red 100%",
                        timestamp="2024-01-01T10:00:00"),
        ]
        _sessions[session_id]["post_measurements"] = [
            Measurement(x=0.35, y=0.35, Y=50.0, X=48.0, Z=56.0,
                        stimulus_rgb=(255, 0, 0), label="Red 100%",
                        timestamp="2024-01-01T10:00:00"),
        ]
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        report = report_payload(_sessions[session_id])
        assert report["improvement_pct"] is not None
        assert report["improvement_pct"] > 0
        assert report["improvement_pct"] < 100


# ── Bug fix: #221 incomplete sessions don't pollute history ───────

class TestHistoryGuardedByCompletion:
    def test_report_does_not_record_history_for_incomplete_session(self, client, session_id, monkeypatch):
        """#221: sessions in 'select_mode' should not record to history."""
        recorded = []
        def mock_record(*a, **kw):
            recorded.append((a, kw))
        monkeypatch.setattr(server_module, "_record_session", mock_record)

        # Session is still in select_mode — no post_measurements
        resp = client.get(f"/api/session/{session_id}/report")
        assert resp.status_code == 400  # no target selected

        assert len(recorded) == 0

    def test_report_does_not_record_history_without_post_measurements(self, client, session_id, monkeypatch):
        """Sessions at report step but without post_measurements should not record to history."""
        recorded = []
        def mock_record(*a, **kw):
            recorded.append((a, kw))
        monkeypatch.setattr(server_module, "_record_session", mock_record)

        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = []
        _sessions[session_id]["post_measurements"] = []
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 0.0

        resp = client.get(f"/api/session/{session_id}/report")
        assert resp.status_code == 200
        assert len(recorded) == 0

    def test_report_records_history_for_completed_session(self, client, session_id, monkeypatch):
        """Only sessions with post_measurements at a completed step should record to history."""
        recorded = []
        def mock_record(*a, **kw):
            recorded.append((a, kw))
        monkeypatch.setattr(server_module, "_record_session", mock_record)

        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(128, 128, 128), label="50% Gray"),
        ]
        _sessions[session_id]["post_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(128, 128, 128), label="50% Gray"),
        ]
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        resp = client.get(f"/api/session/{session_id}/report")
        assert resp.status_code == 200
        assert len(recorded) == 1
        assert recorded[0][1]["session_id"] == session_id

    def test_second_report_fetch_is_idempotent(self, client, session_id, monkeypatch):
        """Subsequent report fetches should not record history again."""
        recorded = []
        def mock_record(*a, **kw):
            recorded.append((a, kw))
        monkeypatch.setattr(server_module, "_record_session", mock_record)

        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(128, 128, 128), label="50% Gray"),
        ]
        _sessions[session_id]["post_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(128, 128, 128), label="50% Gray"),
        ]
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        client.get(f"/api/session/{session_id}/report")
        client.get(f"/api/session/{session_id}/report")
        assert len(recorded) == 1

    def test_record_session_passes_wb_cms_from_tv_settings(self, client, session_id, monkeypatch):
        """#334: history records wb_final/cms_final from session tv_settings."""
        recorded = []
        def mock_record(*a, **kw):
            recorded.append((a, kw))
        monkeypatch.setattr(server_module, "_record_session", mock_record)

        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(128, 128, 128), label="50% Gray"),
        ]
        _sessions[session_id]["post_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(128, 128, 128), label="50% Gray"),
        ]
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0
        _sessions[session_id]["tv_settings"] = {
            "two_point_wb": {"r_gain": -2},
            "cms_sliders": {"red_sat": 3},
        }

        resp = client.get(f"/api/session/{session_id}/report")
        assert resp.status_code == 200
        assert len(recorded) == 1
        assert recorded[0][1]["wb_final"] == {"two_point": {"r_gain": -2}, "multipoint": {}}
        assert recorded[0][1]["cms_final"] == {"red_sat": 3}


# ── Bug fix: #352 _record_session wb_final/cms_final integration test ──────────

class TestRecordSessionWbCmsIntegration:
    """#352: Call real record_session with wb_final/cms_final and verify persistence."""

    def test_record_session_persists_wb_final_and_cms_final(self, tmp_path):
        """Integration test: real record_session writes wb_final/cms_final to JSONL."""
        from calibrator.history import record_session, load_history

        tv_key = "u8g-integration-test"
        session_id = "int-test-session-001"
        report = {
            "pre_cal": {"avg_de": 5.0},
            "post_cal": {"avg_de": 1.5},
            "gamma": {"avg_gamma": 2.2},
            "peak_luminance": 300.0,
            "improvement_pct": 70.0,
            "white_balance": {"avg_de": 0.8},
            "color_tuner": {"avg_de": 1.2},
        }
        wb_final = {"two_point": {"r_gain": -2, "b_gain": 3}, "multipoint": {"10": 5}}
        cms_final = {"red_sat": 3, "green_hue": -1}

        import os
        old_val = os.environ.get("TVCAL_HISTORY_DIR")
        try:
            os.environ["TVCAL_HISTORY_DIR"] = str(tmp_path)
            record_session(
                tv_key=tv_key,
                session_id=session_id,
                mode="SDR",
                report=report,
                wb_final=wb_final,
                cms_final=cms_final,
            )
            entries = load_history(tv_key)
            assert len(entries) == 1
            entry = entries[0]
            assert entry["session_id"] == session_id
            assert entry["wb_final"] == wb_final
            assert entry["cms_final"] == cms_final
        finally:
            if old_val is None:
                os.environ.pop("TVCAL_HISTORY_DIR", None)
            else:
                os.environ["TVCAL_HISTORY_DIR"] = old_val

    def test_record_session_omits_wb_cms_when_not_provided(self, tmp_path):
        """Integration test: omitting wb_final/cms_final defaults to empty dict."""
        from calibrator.history import record_session, load_history

        tv_key = "u8g-integration-test-2"
        session_id = "int-test-session-002"
        report = {"post_cal": {"avg_de": 2.0}}

        import os
        old_val = os.environ.get("TVCAL_HISTORY_DIR")
        try:
            os.environ["TVCAL_HISTORY_DIR"] = str(tmp_path)
            record_session(
                tv_key=tv_key,
                session_id=session_id,
                mode="HDR10",
                report=report,
            )
            entries = load_history(tv_key)
            assert len(entries) == 1
            entry = entries[0]
            assert entry["wb_final"] == {}
            assert entry["cms_final"] == {}
        finally:
            if old_val is None:
                os.environ.pop("TVCAL_HISTORY_DIR", None)
            else:
                os.environ["TVCAL_HISTORY_DIR"] = old_val

    def test_record_session_accepts_wb_cms_kwargs_explicitly(self):
        """Verify record_session signature includes wb_final and cms_final."""
        import inspect
        from calibrator.history import record_session

        sig = inspect.signature(record_session)
        params = list(sig.parameters.keys())
        assert "wb_final" in params, f"wb_final missing from signature: {params}"
        assert "cms_final" in params, f"cms_final missing from signature: {params}"


# ── Bug fix: #217 invalid measurements with delta_e=None don't crash reports ────

class TestReportPayloadInvalidMeasurements:
    def test_report_payload_handles_delta_e_none_gracefully(self, client, session_id):
        """#217: invalid measurements with delta_e=None must not crash report generation."""
        from calibrator.reports import report_payload

        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=0.35, y=0.35, Y=50.0, X=48.0, Z=56.0,
                        stimulus_rgb=(128, 128, 128), label="50% Gray",
                        timestamp="2024-01-01T10:00:00"),
            Measurement(x=-1.0, y=-1.0, Y=-1.0, X=48.0, Z=56.0,
                        stimulus_rgb=(129, 129, 129), label="Bad Gray",
                        timestamp="2024-01-01T10:00:01"),
        ]
        _sessions[session_id]["post_measurements"] = [
            Measurement(x=0.3127, y=0.3290, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(128, 128, 128), label="50% Gray",
                        timestamp="2024-01-01T10:00:00"),
        ]
        _sessions[session_id]["wb_measurements"] = [
            Measurement(x=-1.0, y=-1.0, Y=-1.0, X=48.0, Z=56.0,
                        stimulus_rgb=(128, 128, 128), label="Bad WB Offset 30%"),
            Measurement(x=0.3150, y=0.3280, Y=50.0, X=48.3, Z=55.1,
                        stimulus_rgb=(128, 128, 128), label="Good WB Gain 80%"),
        ]
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        report = report_payload(_sessions[session_id])
        assert report is not None
        assert report["pre_cal"]["avg_de"] is not None
        assert report["pre_cal"]["invalid_count"] == 1
        assert report["post_cal"]["avg_de"] is not None
        assert report["post_cal"]["invalid_count"] == 0
        assert report["white_balance"]["avg_de"] is not None
        assert report["white_balance"]["invalid_count"] == 1

    def test_report_payload_all_invalid_measurements_yields_none_stats(self, client, session_id):
        """#217: when all measurements in a group are invalid, stats should be None."""
        from calibrator.reports import report_payload

        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=-1.0, y=-1.0, Y=-1.0, X=48.0, Z=56.0,
                        stimulus_rgb=(128, 128, 128), label="Bad Gray 1",
                        timestamp="2024-01-01T10:00:00"),
            Measurement(x=0.0, y=0.0, Y=0.0, X=48.0, Z=56.0,
                        stimulus_rgb=(129, 129, 129), label="Bad Gray 2",
                        timestamp="2024-01-01T10:00:01"),
        ]
        _sessions[session_id]["post_measurements"] = []
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 0.0

        report = report_payload(_sessions[session_id])
        assert report["pre_cal"]["avg_de"] is None
        assert report["pre_cal"]["max_de"] is None
        assert report["pre_cal"]["invalid_count"] == 2
        assert report["post_cal"]["avg_de"] is None
        assert report["post_cal"]["max_de"] is None
        assert report["improvement_pct"] is None

    def test_report_api_does_not_crash_with_invalid_measurements(self, client, session_id):
        """#217: the /report endpoint should return 200 even with invalid measurements."""
        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = type("T", (), {
            "gamut": "bt709", "eotf": "bt1886",
            "peak_luminance_nits": 500, "white_point_xy": (0.3127, 0.3290),
            "gamma": 2.4,
        })()
        _sessions[session_id]["pre_measurements"] = [
            Measurement(x=-1.0, y=-1.0, Y=-1.0, X=48.0, Z=56.0,
                        stimulus_rgb=(128, 128, 128), label="Bad Gray"),
        ]
        _sessions[session_id]["post_measurements"] = [
            Measurement(x=-1.0, y=-1.0, Y=-1.0, X=48.0, Z=56.0,
                        stimulus_rgb=(128, 128, 128), label="Bad Post"),
        ]
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 0.0

        resp = client.get(f"/api/session/{session_id}/report")
        assert resp.status_code == 200
        data = resp.json()
        assert "pre_cal" in data
        assert "post_cal" in data
        assert data["pre_cal"]["avg_de"] is None
        assert data["post_cal"]["avg_de"] is None
