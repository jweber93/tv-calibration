"""
Shared pytest fixtures for the tv-calibration test suite.

Hardware interfaces (ADB subprocess, ZRO bridge HTTP, file watcher singleton)
are stubbed here so individual test files don't duplicate mock setup.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ── File watcher singleton reset ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_file_watcher():
    """Reset file watcher singleton state before and after every test.

    calibrator/file_watcher.py holds module-level singleton state (_observer,
    _subscribers, _last_import, _watcher_error).  Any test that calls a
    /api/watch/* endpoint or start_watching() directly leaves residual state
    that affects subsequent tests regardless of ordering.  Making this autouse
    ensures isolation without requiring each test file to remember to clean up.

    test_file_watcher.py has its own clean_watcher autouse fixture — running
    both is harmless because stop_watching() and list.clear() are idempotent.
    """
    import calibrator.file_watcher as fw

    fw.stop_watching()
    with fw._sub_lock:
        fw._subscribers.clear()
    fw._last_import = None
    fw._watcher_error = None

    yield

    fw.stop_watching()
    with fw._sub_lock:
        fw._subscribers.clear()


# ── ADB / subprocess fixtures ─────────────────────────────────────────────────

def _adb_result(stdout: str = "", returncode: int = 0, stderr: str = ""):
    """Build a mock subprocess.CompletedProcess for ADB command results."""
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.stdout = stdout
    result.returncode = returncode
    result.stderr = stderr
    return result


@pytest.fixture
def adb_device_found():
    """Patch subprocess.run: one ADB device is attached and online."""
    output = "List of devices attached\n192.168.1.100:5555\tdevice\n"
    with patch("subprocess.run", return_value=_adb_result(stdout=output)) as mock:
        yield mock


@pytest.fixture
def adb_no_device():
    """Patch subprocess.run: no ADB devices attached."""
    output = "List of devices attached\n"
    with patch("subprocess.run", return_value=_adb_result(stdout=output)) as mock:
        yield mock


@pytest.fixture
def adb_silent():
    """Patch subprocess.run: commands succeed with empty output (set/apply ops)."""
    with patch("subprocess.run", return_value=_adb_result()) as mock:
        yield mock


# ── ZRO Bridge / httpx fixtures ───────────────────────────────────────────────

def _httpx_response(json_data: dict, status_code: int = 200):
    """Build a mock httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock(text="bridge error")
        )
    return resp


@pytest.fixture
def zro_bridge_up(monkeypatch):
    """Patch httpx.get and httpx.post: ZRO bridge is reachable and healthy."""
    def _get(url, **kwargs):
        return _httpx_response({"ok": True, "version": "1.0", "backend": "pyautogui"})

    def _post(url, **kwargs):
        return _httpx_response({"result": "accepted"})

    monkeypatch.setattr("httpx.get", _get)
    monkeypatch.setattr("httpx.post", _post)


@pytest.fixture
def zro_bridge_down(monkeypatch):
    """Patch httpx.get and httpx.post: ZRO bridge connection refused."""
    import httpx

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr("httpx.get", _raise)
    monkeypatch.setattr("httpx.post", _raise)


# ── FastAPI test client ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_client():
    """Session-scoped FastAPI test client.

    Named api_client (not client) to avoid shadowing the function-scoped
    client fixtures defined in test_server_api.py and test_adb_control.py.
    Use this fixture in new test files; prefer the existing client fixture
    in files that already define one.
    """
    from fastapi.testclient import TestClient
    from server import app

    with TestClient(app) as client:
        yield client
