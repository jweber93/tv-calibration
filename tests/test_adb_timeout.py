"""Tests for TimeoutExpired resilience in calibrator/adb_control.py.

Covers:
  - _adb returns rc=124 on timeout (not raising)
  - set_cms_value returns {"ok": False} on timeout
  - reset_cms stops on first timeout and returns partial state
  - push_cms_tool returns {"ok": False} on timeout
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import calibrator.adb_control as adb_mod


# ── Helpers ──────────────────────────────────────────────────────────────────

def _completed(returncode=0, stdout="", stderr=""):
    return MagicMock(
        spec=subprocess.CompletedProcess,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ── _adb timeout ─────────────────────────────────────────────────────────────

class TestAdbTimeout:
    """Verify _adb catches TimeoutExpired and returns a degraded CompletedProcess."""

    def test_returns_rc_124_on_timeout(self):
        exc = subprocess.TimeoutExpired(cmd=["adb", "shell", "test"], timeout=15)
        with patch("subprocess.run", side_effect=exc):
            result = adb_mod._adb("shell", "test")
        assert result.returncode == 124

    def test_stderr_contains_timeout_details(self):
        exc = subprocess.TimeoutExpired(cmd=["adb", "shell", "get_hue"], timeout=15)
        with patch("subprocess.run", side_effect=exc):
            result = adb_mod._adb("shell", "get_hue")
        assert "timed out" in result.stderr
        assert "15s" in result.stderr
        assert "get_hue" in result.stderr

    def test_stdout_decoded_when_present(self):
        exc = subprocess.TimeoutExpired(
            cmd=["adb", "shell"], timeout=15,
            output=b"partial output",
        )
        with patch("subprocess.run", side_effect=exc):
            result = adb_mod._adb("shell")
        assert result.stdout == "partial output"

    def test_normal_call_unchanged(self):
        mock = _completed(returncode=0, stdout="OK", stderr="")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod._adb("shell", "test")
        assert result.returncode == 0
        assert result.stdout == "OK"


# ── set_cms_value timeout ────────────────────────────────────────────────────

class TestSetCmsValueTimeout:
    """set_cms_value should return {"ok": False} on timeout, not raise."""

    def test_returns_ok_false_on_timeout(self):
        exc = subprocess.TimeoutExpired(cmd=["adb", "shell"], timeout=15)
        with patch("subprocess.run", side_effect=exc):
            result = adb_mod.set_cms_value("Red", "Hue", 3)
        assert result["ok"] is False

    def test_error_message_in_stderr(self):
        exc = subprocess.TimeoutExpired(cmd=["adb", "shell"], timeout=15)
        with patch("subprocess.run", side_effect=exc):
            result = adb_mod.set_cms_value("Green", "Saturation", -5)
        assert "timed out" in result["stderr"]

    def test_normal_call_still_works(self):
        mock = _completed(returncode=0, stdout="OK set_hue ch=1 val=3 ret=0")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.set_cms_value("Red", "Hue", 3)
        assert result["ok"] is True


# ── push_cms_tool timeout ───────────────────────────────────────────────────

class TestPushCmsToolTimeout:
    """push_cms_tool should return {"ok": False} on timeout, not raise."""

    def test_returns_ok_false_on_timeout(self):
        exc = subprocess.TimeoutExpired(cmd=["adb", "push"], timeout=30)
        with patch("subprocess.run", side_effect=exc):
            result = adb_mod.push_cms_tool()
        assert result["ok"] is False

    def test_error_message_in_stderr(self):
        exc = subprocess.TimeoutExpired(cmd=["adb", "push"], timeout=30)
        with patch("subprocess.run", side_effect=exc), \
             patch("pathlib.Path.exists", return_value=True):
            result = adb_mod.push_cms_tool()
        assert "timed out" in result["stderr"]
        assert "30s" in result["stderr"]

    def test_normal_call_still_works(self):
        mock = _completed(returncode=0, stdout="1 file pushed", stderr="")
        with patch("subprocess.run", return_value=mock), \
             patch("pathlib.Path.exists", return_value=True):
            result = adb_mod.push_cms_tool()
        assert result["ok"] is True


# ── reset_cms partial state ─────────────────────────────────────────────────

class TestResetCmsPartialState:
    """On first timeout, reset_cms stops and returns partial progress."""

    def test_stops_on_first_timeout(self):
        exc = subprocess.TimeoutExpired(cmd=["adb", "shell"], timeout=15)
        # First call succeeds, second call times out
        responses = [
            _completed(returncode=0, stdout="OK set_hue ch=1 val=0 ret=0"),
        ] + [exc] * 100
        with patch("subprocess.run", side_effect=responses) as run:
            result = adb_mod.reset_cms()
        # Only 2 calls made (first succeeded, second timed out)
        assert run.call_count == 2
        assert result["ok"] is False
        assert result["partial"] is True
        assert result["failed_at"] == "Red/set_saturation"
        assert len(result["completed"]) == 1
        assert "Red/set_hue" in result["completed"]

    def test_timeout_on_last_channel(self):
        exc = subprocess.TimeoutExpired(cmd=["adb", "shell"], timeout=15)
        # All 17 calls succeed, 18th (Magenta/set_brightness) times out
        responses = [
            _completed(returncode=0, stdout="OK set_hue ch=1 val=0 ret=0")
            for _ in range(17)
        ] + [exc]
        with patch("subprocess.run", side_effect=responses):
            result = adb_mod.reset_cms()
        assert result["ok"] is False
        assert result["partial"] is True
        assert result["failed_at"] == "Magenta/set_brightness"
        assert len(result["completed"]) == 17

    def test_normal_call_still_works(self):
        mock = _completed(returncode=0, stdout="OK set_hue ch=1 val=0 ret=0")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.reset_cms()
        assert result["ok"] is True
        assert "partial" not in result
