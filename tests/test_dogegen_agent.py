"""
Tests for the Dogegen Companion Agent (tools/dogegen-agent/agent.py).

Mocks subprocess.Popen/tasklist/pgrep so no real Dogegen.exe or Windows
process table is required. Covers start/stop/status incl. "already
running" (external PID) and "exe not found" paths — see issue #591.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "dogegen-agent"))

import agent  # noqa: E402

client = TestClient(agent.app)


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with a clean DogegenState and default config."""
    agent._dogegen_state = agent.DogegenState()
    agent._config = dict(agent.DEFAULT_CONFIG)
    yield
    agent._dogegen_state = agent.DogegenState()
    agent._config = dict(agent.DEFAULT_CONFIG)


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
