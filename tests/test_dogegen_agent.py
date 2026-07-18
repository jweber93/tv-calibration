"""
Tests for the Dogegen Companion Agent (tools/dogegen-agent/agent.py).

Mocks subprocess.Popen/tasklist/pgrep so no real Dogegen.exe or Windows
process table is required. Covers start/stop/status incl. "already
running" (external PID) and "exe not found" paths — see issue #591.
"""

import os
import socket
import struct
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "dogegen-agent"))

import agent  # noqa: E402
import resolve_protocol  # noqa: E402

client = TestClient(agent.app)


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with a clean DogegenState, default config, and a
    fresh (unstarted) ResolveServer so no test binds a real port or leaks
    a background thread — tests that need one either mock send_patch/status
    or construct their own real ResolveServer explicitly."""
    agent._dogegen_state = agent.DogegenState()
    agent._config = dict(agent.DEFAULT_CONFIG)
    agent._resolve_server = agent.ResolveServer()
    yield
    agent._dogegen_state = agent.DogegenState()
    agent._config = dict(agent.DEFAULT_CONFIG)
    agent._resolve_server = agent.ResolveServer()


def _mock_proc(pid=1234, running=True):
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = None if running else 0
    return proc


# ── find_dogegen_executable ─────────────────────────────────────────────────

class TestFindExecutable:
    def test_uses_configured_path_if_exists(self, tmp_path):
        exe = tmp_path / "Dogegen.exe"
        exe.write_text("")
        cfg = {"path": str(exe)}
        assert agent.find_dogegen_executable(cfg) == str(exe)

    def test_falls_back_to_path_env_when_not_configured(self):
        cfg = {"path": ""}
        with patch("shutil.which", side_effect=lambda name: "/usr/bin/dogegen.exe" if "dogegen" in name.lower() else None), \
             patch.object(Path, "exists", return_value=True):
            result = agent.find_dogegen_executable(cfg)
        assert result is not None

    def test_returns_none_when_nothing_found(self):
        cfg = {"path": "/nonexistent/Dogegen.exe"}
        with patch("shutil.which", return_value=None):
            assert agent.find_dogegen_executable(cfg) is None


# ── external_dogegen_pid ─────────────────────────────────────────────────────

class TestExternalPid:
    def test_windows_tasklist_found(self):
        proc = MagicMock()
        proc.stdout = '"Dogegen.exe","4321","Console","1","50,000 K"\n'
        with patch.object(os, "name", "nt"), patch("subprocess.run", return_value=proc):
            assert agent.external_dogegen_pid() == 4321

    def test_windows_tasklist_no_tasks(self):
        proc = MagicMock()
        proc.stdout = "INFO: No tasks are running which match the specified criteria.\n"
        with patch.object(os, "name", "nt"), patch("subprocess.run", return_value=proc):
            assert agent.external_dogegen_pid() is None

    def test_linux_pgrep_found(self):
        proc = MagicMock()
        proc.stdout = "9876\n"
        with patch.object(os, "name", "posix"), patch("subprocess.run", return_value=proc):
            assert agent.external_dogegen_pid() == 9876

    def test_linux_pgrep_not_found(self):
        proc = MagicMock()
        proc.stdout = ""
        with patch.object(os, "name", "posix"), patch("subprocess.run", return_value=proc):
            assert agent.external_dogegen_pid() is None

    def test_subprocess_exception_returns_none(self):
        with patch("subprocess.run", side_effect=OSError("no such tool")):
            assert agent.external_dogegen_pid() is None

    def test_logs_info_when_external_pid_detected(self, caplog):
        proc = MagicMock()
        proc.stdout = "4321\n"
        with caplog.at_level("INFO", logger="agent"), \
             patch.object(os, "name", "posix"), patch("subprocess.run", return_value=proc):
            agent.external_dogegen_pid()
        assert any("4321" in rec.message for rec in caplog.records)


# ── GET /status ──────────────────────────────────────────────────────────────

class TestStatus:
    def test_not_configured(self):
        with patch.object(agent, "find_dogegen_executable", return_value=None), \
             patch.object(agent, "external_dogegen_pid", return_value=None):
            r = client.get("/status")
        assert r.status_code == 200
        d = r.json()
        assert d["configured"] is False
        assert d["running"] is False
        assert d["managed"] is False
        assert d["ready"] is False
        assert d["pid"] is None

    def test_reports_defaults_shape(self):
        with patch.object(agent, "find_dogegen_executable", return_value=None), \
             patch.object(agent, "external_dogegen_pid", return_value=None):
            r = client.get("/status")
        d = r.json()
        for key in (
            "running", "managed", "ready", "ready_in_ms", "pid", "path",
            "configured", "last_error", "launch_cmd", "resolve_host",
            "window_pct", "maxcll",
        ):
            assert key in d

    def test_external_process_detected_is_ready_immediately(self):
        with patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"), \
             patch.object(agent, "external_dogegen_pid", return_value=555):
            r = client.get("/status")
        d = r.json()
        assert d["running"] is True
        assert d["managed"] is False
        assert d["pid"] == 555
        assert d["ready"] is True

    def test_managed_process_not_ready_before_delay(self):
        agent._dogegen_state.set_started(_mock_proc(pid=42), agent._now(), ["Dogegen.exe"])
        with patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"):
            r = client.get("/status")
        d = r.json()
        assert d["running"] is True
        assert d["managed"] is True
        assert d["pid"] == 42
        assert d["ready"] is False
        assert d["ready_in_ms"] > 0

    def test_managed_process_ready_after_delay(self):
        started_at = agent._now() - timedelta(seconds=10)
        agent._dogegen_state.set_started(_mock_proc(pid=42), started_at, ["Dogegen.exe"])
        with patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"):
            r = client.get("/status")
        d = r.json()
        assert d["ready"] is True
        assert d["ready_in_ms"] == 0


# ── POST /start ──────────────────────────────────────────────────────────────

class TestStart:
    def test_exe_not_found_returns_400(self):
        with patch.object(agent, "find_dogegen_executable", return_value=None), \
             patch.object(agent, "external_dogegen_pid", return_value=None):
            r = client.post("/start", json={"mode": "HDR10"})
        assert r.status_code == 400
        assert "not found" in r.json()["detail"].lower()
        assert agent._dogegen_state.get_last_error() is not None

    def test_already_running_externally(self):
        with patch.object(agent, "external_dogegen_pid", return_value=777), \
             patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"):
            r = client.post("/start", json={"mode": "SDR"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["already_running"] is True
        assert d["pid"] == 777

    def test_already_running_managed(self):
        agent._dogegen_state.set_started(_mock_proc(pid=99), agent._now(), ["Dogegen.exe"])
        with patch.object(agent, "external_dogegen_pid", return_value=None), \
             patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"):
            r = client.post("/start", json={"mode": "SDR"})
        assert r.status_code == 200
        d = r.json()
        assert d["already_running"] is True
        assert d["pid"] == 99

    def test_starts_hdr10_with_expected_cmd(self):
        agent._config["resolve_host"] = "192.168.1.5"
        agent._config["window_pct"] = 25
        agent._config["maxcll"] = 4000
        new_proc = _mock_proc(pid=111)
        with patch.object(agent, "external_dogegen_pid", return_value=None), \
             patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"), \
             patch("subprocess.Popen", return_value=new_proc) as mock_popen:
            r = client.post("/start", json={"mode": "HDR10"})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["already_running"] is False
        assert d["pid"] == 111
        assert d["managed"] is True
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "C:/Dogegen.exe"
        assert "mode 10_hdr" in cmd
        assert "maxcll 4000" in cmd
        assert "resolve_hdr 192.168.1.5 25" in cmd

    def test_starts_sdr_with_expected_cmd(self):
        agent._config["resolve_host"] = ""
        new_proc = _mock_proc(pid=222)
        with patch.object(agent, "external_dogegen_pid", return_value=None), \
             patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"), \
             patch("subprocess.Popen", return_value=new_proc) as mock_popen:
            r = client.post("/start", json={"mode": "SDR"})
        assert r.status_code == 200
        cmd = mock_popen.call_args[0][0]
        assert "resolve_sdr 127.0.0.1" in cmd

    def test_start_failure_sets_last_error_and_returns_500(self):
        with patch.object(agent, "external_dogegen_pid", return_value=None), \
             patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"), \
             patch("subprocess.Popen", side_effect=OSError("permission denied")):
            r = client.post("/start", json={"mode": "SDR"})
        assert r.status_code == 500
        assert agent._dogegen_state.get_last_error() == "permission denied"

    def test_start_defaults_mode_to_none(self):
        new_proc = _mock_proc(pid=333)
        with patch.object(agent, "external_dogegen_pid", return_value=None), \
             patch.object(agent, "find_dogegen_executable", return_value="C:/Dogegen.exe"), \
             patch("subprocess.Popen", return_value=new_proc) as mock_popen:
            r = client.post("/start", json={})
        assert r.status_code == 200
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["C:/Dogegen.exe"]

    def test_start_rejects_invalid_mode(self):
        r = client.post("/start", json={"mode": "Hdr10"})
        assert r.status_code == 422


# ── POST /stop ───────────────────────────────────────────────────────────────

class TestStop:
    def test_already_stopped(self):
        r = client.post("/stop")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["already_stopped"] is True

    def test_stops_managed_process(self):
        proc = _mock_proc(pid=42)
        agent._dogegen_state.set_started(proc, agent._now(), ["Dogegen.exe"])
        r = client.post("/stop")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["already_stopped"] is False
        proc.terminate.assert_called_once()
        assert agent._dogegen_state.is_running() is False

    def test_stop_falls_back_to_kill_on_terminate_timeout(self):
        import subprocess as sp
        proc = _mock_proc(pid=42)
        proc.wait.side_effect = sp.TimeoutExpired(cmd="Dogegen.exe", timeout=3)
        agent._dogegen_state.set_started(proc, agent._now(), ["Dogegen.exe"])
        r = client.post("/stop")
        assert r.status_code == 200
        proc.kill.assert_called_once()


# ── _resolve_connect_target / dogegen_command_for_mode (#630) ─────────────────

class TestResolveConnectTarget:
    def test_empty_host_default_port_omits_target(self):
        """Byte-for-byte today's behavior: no ColourSpace/agent listen-port
        override configured -> Dogegen falls back to its own 127.0.0.1:20002
        default, so no host:port token is needed on the command line."""
        cfg = {"resolve_host": "", "resolve_listen_port": agent.DEFAULT_RESOLVE_PORT}
        assert agent._resolve_connect_target(cfg) == ""

    def test_empty_host_missing_port_key_omits_target(self):
        assert agent._resolve_connect_target({"resolve_host": ""}) == ""

    def test_empty_host_custom_port_targets_localhost(self):
        cfg = {"resolve_host": "", "resolve_listen_port": 20005}
        assert agent._resolve_connect_target(cfg) == "127.0.0.1:20005"

    def test_explicit_host_wins_over_custom_listen_port(self):
        cfg = {"resolve_host": "192.168.1.5", "resolve_listen_port": 20005}
        assert agent._resolve_connect_target(cfg) == "192.168.1.5"

    def test_explicit_host_wins_over_default_listen_port(self):
        cfg = {"resolve_host": "192.168.1.5", "resolve_listen_port": agent.DEFAULT_RESOLVE_PORT}
        assert agent._resolve_connect_target(cfg) == "192.168.1.5"


class TestDogegenCommandForModeCustomListenPort:
    def test_hdr10_with_custom_listen_port_and_no_resolve_host(self):
        cfg = {"resolve_host": "", "resolve_listen_port": 20005, "window_pct": 10, "maxcll": 1000}
        cmd = agent.dogegen_command_for_mode("HDR10", "C:/Dogegen.exe", cfg)
        assert "resolve_hdr 127.0.0.1:20005 10" in cmd

    def test_sdr_with_custom_listen_port_and_no_resolve_host(self):
        cfg = {"resolve_host": "", "resolve_listen_port": 20005, "maxcll": 1000}
        cmd = agent.dogegen_command_for_mode("SDR", "C:/Dogegen.exe", cfg)
        assert "resolve_sdr 127.0.0.1:20005" in cmd

    def test_hdr10_default_listen_port_unchanged_from_today(self):
        cfg = {"resolve_host": "", "resolve_listen_port": agent.DEFAULT_RESOLVE_PORT,
               "window_pct": 25, "maxcll": 1000}
        cmd = agent.dogegen_command_for_mode("HDR10", "C:/Dogegen.exe", cfg)
        assert "resolve_hdr 25" in cmd

    def test_resolve_host_ignores_custom_listen_port(self):
        cfg = {"resolve_host": "192.168.1.5", "resolve_listen_port": 20005,
               "window_pct": 10, "maxcll": 1000}
        cmd = agent.dogegen_command_for_mode("HDR10", "C:/Dogegen.exe", cfg)
        assert "resolve_hdr 192.168.1.5 10" in cmd
        assert not any("20005" in part for part in cmd)


# ── GET /status includes Resolve server fields (#630) ──────────────────────────

class TestStatusIncludesResolveFields:
    def test_status_merges_resolve_server_status(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.status.return_value = {
            "resolve_listening": True,
            "resolve_connected": True,
            "resolve_peer": "192.168.1.9:54321",
            "resolve_listen_port": 20002,
            "resolve_last_error": None,
        }
        with patch.object(agent, "find_dogegen_executable", return_value=None), \
             patch.object(agent, "external_dogegen_pid", return_value=None):
            r = client.get("/status")
        assert r.status_code == 200
        d = r.json()
        assert d["resolve_listening"] is True
        assert d["resolve_connected"] is True
        assert d["resolve_peer"] == "192.168.1.9:54321"
        assert d["resolve_listen_port"] == 20002

    def test_real_unstarted_resolve_server_reports_not_listening(self):
        """The fixture's fresh ResolveServer() is never start()ed, so /status
        must not hang or error -- it just reports the honest default state."""
        with patch.object(agent, "find_dogegen_executable", return_value=None), \
             patch.object(agent, "external_dogegen_pid", return_value=None):
            r = client.get("/status")
        d = r.json()
        assert d["resolve_listening"] is False
        assert d["resolve_connected"] is False
        assert d["resolve_peer"] is None


# ── POST /patch (#630) ──────────────────────────────────────────────────────────

class TestPatch:
    def test_patch_success_returns_ok(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.send_patch.return_value = {"ok": True}
        r = client.post("/patch", json={"r": 512, "g": 512, "b": 512, "bits": 10})
        assert r.status_code == 200
        assert r.json() == {"ok": True}

    def test_patch_passes_expected_kwargs_with_defaults(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.send_patch.return_value = {"ok": True}
        client.post("/patch", json={"r": 100, "g": 150, "b": 200})
        _, kwargs = agent._resolve_server.send_patch.call_args
        assert kwargs["r"] == 100
        assert kwargs["g"] == 150
        assert kwargs["b"] == 200
        assert kwargs["bits"] == 8
        assert kwargs["size_pct"] is None
        assert (kwargs["x"], kwargs["y"], kwargs["cx"], kwargs["cy"]) == (0.0, 0.0, 1.0, 1.0)
        assert kwargs["bg"] == (0, 0, 0)
        assert kwargs["bg_bits"] is None

    def test_patch_passes_through_size_pct_and_background(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.send_patch.return_value = {"ok": True}
        client.post("/patch", json={
            "r": 1023, "g": 0, "b": 0, "bits": 10, "size_pct": 18,
            "bg_r": 10, "bg_g": 20, "bg_b": 30, "bg_bits": 8,
        })
        _, kwargs = agent._resolve_server.send_patch.call_args
        assert kwargs["size_pct"] == 18
        assert kwargs["bg"] == (10, 20, 30)
        assert kwargs["bg_bits"] == 8

    def test_patch_not_connected_returns_409(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.send_patch.return_value = {
            "ok": False, "error_type": "not_connected", "error": "Dogegen is not connected.",
        }
        r = client.post("/patch", json={"r": 0, "g": 0, "b": 0})
        assert r.status_code == 409
        # Structured JSON body, not FastAPI's default {"detail": "..."}
        # envelope (#631 review) -- error_type is a stable field callers can
        # branch on instead of pattern-matching the human-readable message.
        assert r.json() == {
            "ok": False, "error_type": "not_connected", "error": "Dogegen is not connected.",
        }

    def test_patch_invalid_patch_returns_400(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.send_patch.return_value = {
            "ok": False, "error_type": "invalid_patch", "error": "bits must be 8 or 10, got 12",
        }
        r = client.post("/patch", json={"r": 0, "g": 0, "b": 0, "bits": 12})
        assert r.status_code == 400
        assert r.json()["ok"] is False
        assert r.json()["error_type"] == "invalid_patch"

    def test_patch_timeout_returns_504(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.send_patch.return_value = {
            "ok": False, "error_type": "timeout", "error": "Timed out sending patch after 2.0s.",
        }
        r = client.post("/patch", json={"r": 0, "g": 0, "b": 0})
        assert r.status_code == 504
        assert r.json()["error_type"] == "timeout"

    def test_patch_send_failed_returns_502(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.send_patch.return_value = {
            "ok": False, "error_type": "send_failed", "error": "Failed to send patch: broken pipe",
        }
        r = client.post("/patch", json={"r": 0, "g": 0, "b": 0})
        assert r.status_code == 502
        assert r.json()["error_type"] == "send_failed"

    def test_patch_unknown_error_type_falls_back_to_502(self):
        agent._resolve_server = MagicMock()
        agent._resolve_server.send_patch.return_value = {
            "ok": False, "error_type": "something_new", "error": "mystery failure",
        }
        r = client.post("/patch", json={"r": 0, "g": 0, "b": 0})
        assert r.status_code == 502
        assert r.json() == {"ok": False, "error_type": "something_new", "error": "mystery failure"}

    def test_patch_missing_required_field_returns_422(self):
        r = client.post("/patch", json={"r": 0, "g": 0})
        assert r.status_code == 422

    def test_patch_against_real_resolve_server_without_dogegen_is_409(self):
        """End-to-end through the real (fixture-provided, unstarted)
        ResolveServer rather than a mock -- confirms the /patch wiring
        itself, not just the mocked contract."""
        r = client.post("/patch", json={"r": 0, "g": 0, "b": 0})
        assert r.status_code == 409
        assert r.json()["error_type"] == "not_connected"


# ── POST /patch full-stack integration (#631 review) ────────────────────────
#
# The tests above mock _resolve_server.send_patch, which verifies the HTTP
# layer's contract but not that a byte actually reaches a real connected
# Dogegen. This drives the real stack: HTTP POST /patch -> the real
# ResolveServer.send_patch -> a real loopback socket -> a fake "Dogegen"
# client that reads the frame back and decodes it.

class TestPatchFullStack:
    def test_http_patch_delivers_correct_bytes_to_real_connection(self):
        real_server = agent.ResolveServer(host="127.0.0.1", port=0)
        real_server.start()
        agent._resolve_server = real_server
        fake_dogegen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            fake_dogegen.connect(("127.0.0.1", real_server.port))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not real_server.status()["resolve_connected"]:
                time.sleep(0.01)
            assert real_server.status()["resolve_connected"] is True

            r = client.post("/patch", json={
                "r": 700, "g": 200, "b": 900, "bits": 10, "bg_r": 1, "bg_g": 2, "bg_b": 3,
            })
            assert r.status_code == 200
            assert r.json() == {"ok": True}

            fake_dogegen.settimeout(2.0)
            header = fake_dogegen.recv(4)
            (length,) = struct.unpack("!i", header)
            payload = b""
            while len(payload) < length:
                payload += fake_dogegen.recv(length - len(payload))
            decoded = resolve_protocol.decode_patch_frame(header + payload)
            assert decoded["r"] == 700
            assert decoded["g"] == 200
            assert decoded["b"] == 900
            assert decoded["bits"] == 10
            assert decoded["bg"] == (1, 2, 3)
        finally:
            fake_dogegen.close()
            real_server.stop()
