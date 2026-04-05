"""Tests for calibrator/adb_control.py and the /api/adb/* endpoints."""
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import calibrator.adb_control as adb_mod
from server import app, _sessions
import server as server_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_sessions():
    _sessions.clear()
    server_module._watched_session_id = None
    yield
    _sessions.clear()
    server_module._watched_session_id = None


@pytest.fixture
def client():
    return TestClient(app)


def _completed(returncode=0, stdout="", stderr=""):
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ── Unit tests: adb_control module ────────────────────────────────────────────

class TestGetConnectedDevices:
    def test_parses_device_list(self):
        output = "List of devices attached\n192.168.1.100:5555\tdevice\n"
        with patch("subprocess.run", return_value=_completed(stdout=output)):
            assert adb_mod.get_connected_devices() == ["192.168.1.100:5555"]

    def test_empty_when_no_devices(self):
        output = "List of devices attached\n"
        with patch("subprocess.run", return_value=_completed(stdout=output)):
            assert adb_mod.get_connected_devices() == []

    def test_ignores_offline_devices(self):
        output = "List of devices attached\n192.168.1.100:5555\toffline\n"
        with patch("subprocess.run", return_value=_completed(stdout=output)):
            assert adb_mod.get_connected_devices() == []

    def test_multiple_devices(self):
        output = (
            "List of devices attached\n"
            "192.168.1.100:5555\tdevice\n"
            "192.168.1.101:5555\tdevice\n"
        )
        with patch("subprocess.run", return_value=_completed(stdout=output)):
            devices = adb_mod.get_connected_devices()
            assert devices == ["192.168.1.100:5555", "192.168.1.101:5555"]


class TestIsCmsToolDeployed:
    def test_deployed(self):
        with patch("subprocess.run", return_value=_completed(returncode=0, stdout="/data/local/tmp/cms_tool.dex\n")):
            assert adb_mod.is_cms_tool_deployed() is True

    def test_not_deployed(self):
        with patch("subprocess.run", return_value=_completed(returncode=1, stderr="No such file")):
            assert adb_mod.is_cms_tool_deployed() is False


class TestSetCmsValue:
    def test_valid_call_uses_app_process(self):
        mock = _completed(stdout="OK set_hue ch=1 val=3 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            result = adb_mod.set_cms_value("Red", "Hue", 3)
        assert result["ok"] is True
        # Verify app_process is invoked, not am start
        shell_cmd = run.call_args[0][0]
        assert "app_process" in " ".join(shell_cmd)
        assert "am start" not in " ".join(shell_cmd)
        assert "set_hue" in " ".join(shell_cmd)
        assert "1" in " ".join(shell_cmd)   # channel 1 = Red
        assert "3" in " ".join(shell_cmd)   # value

    def test_saturation_uses_correct_action(self):
        mock = _completed(stdout="OK set_saturation ch=5 val=-2 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            adb_mod.set_cms_value("Cyan", "Saturation", -2)
        shell_cmd = " ".join(run.call_args[0][0])
        assert "set_saturation" in shell_cmd
        assert "5" in shell_cmd   # channel 5 = Cyan
        assert "-2" in shell_cmd

    def test_brightness_uses_correct_action(self):
        mock = _completed(stdout="OK set_brightness ch=2 val=10 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            adb_mod.set_cms_value("Green", "Brightness", 10)
        shell_cmd = " ".join(run.call_args[0][0])
        assert "set_brightness" in shell_cmd
        assert "2" in shell_cmd   # channel 2 = Green

    def test_passes_device_serial(self):
        mock = _completed(stdout="OK set_hue ch=3 val=0 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            adb_mod.set_cms_value("Blue", "Hue", 0, device="192.168.1.5:5555")
        cmd = run.call_args[0][0]
        assert "-s" in cmd
        assert "192.168.1.5:5555" in cmd

    def test_all_six_channels_resolve(self):
        for colour, channel_id in adb_mod.CMS_CHANNELS.items():
            mock = _completed(stdout=f"OK set_hue ch={channel_id} val=0 ret=0")
            with patch("subprocess.run", return_value=mock) as run:
                adb_mod.set_cms_value(colour, "Hue", 0)
            assert str(channel_id) in " ".join(run.call_args[0][0])

    def test_raises_on_unknown_channel(self):
        with pytest.raises(ValueError, match="Unknown channel"):
            adb_mod.set_cms_value("Purple", "Hue", 0)

    def test_raises_on_unknown_control(self):
        with pytest.raises(ValueError, match="Unknown control"):
            adb_mod.set_cms_value("Red", "Tint", 0)

    def test_raises_on_out_of_range_value_high(self):
        with pytest.raises(ValueError, match="out of range"):
            adb_mod.set_cms_value("Red", "Hue", 11)

    def test_raises_on_out_of_range_value_low(self):
        with pytest.raises(ValueError, match="out of range"):
            adb_mod.set_cms_value("Red", "Hue", -11)

    def test_boundary_values_accepted(self):
        for v in (adb_mod.CMS_MIN, 0, adb_mod.CMS_MAX):
            mock = _completed(stdout=f"OK set_hue ch=1 val={v} ret=0")
            with patch("subprocess.run", return_value=mock):
                result = adb_mod.set_cms_value("Red", "Hue", v)
            assert result["ok"] is True

    def test_ok_false_if_stdout_not_ok(self):
        mock = _completed(returncode=0, stdout="ERROR: something wrong")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.set_cms_value("Red", "Hue", 0)
        assert result["ok"] is False


class TestGetCmsValue:
    def test_parses_value_line(self):
        mock = _completed(stdout="VALUE 5\n")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.get_cms_value("Red", "Hue")
        assert result["ok"] is True
        assert result["value"] == 5

    def test_parses_negative_value(self):
        mock = _completed(stdout="VALUE -3\n")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.get_cms_value("Cyan", "Saturation")
        assert result["value"] == -3

    def test_raises_on_unknown_channel(self):
        with pytest.raises(ValueError, match="Unknown channel"):
            adb_mod.get_cms_value("Purple", "Hue")

    def test_raises_on_unknown_control(self):
        with pytest.raises(ValueError, match="Unknown control"):
            adb_mod.get_cms_value("Red", "Tint")


class TestGetAllCmsValues:
    def test_parses_all_channels(self):
        output = (
            "Red hue=5 sat=0 bri=0\n"
            "Green hue=0 sat=-3 bri=0\n"
            "Blue hue=0 sat=0 bri=2\n"
            "Yellow hue=0 sat=0 bri=0\n"
            "Cyan hue=0 sat=0 bri=0\n"
            "Magenta hue=0 sat=0 bri=0\n"
        )
        with patch("subprocess.run", return_value=_completed(stdout=output)):
            result = adb_mod.get_all_cms_values()
        assert result["ok"] is True
        assert result["values"]["Red"]["hue"] == 5
        assert result["values"]["Green"]["sat"] == -3
        assert result["values"]["Blue"]["bri"] == 2


class TestResetCms:
    def test_sets_all_channels_to_zero(self):
        mock = _completed(stdout="OK set_hue ch=1 val=0 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            result = adb_mod.reset_cms()
        assert result["ok"] is True
        # 6 channels × 3 controls = 18 calls
        assert run.call_count == 18

    def test_ok_false_on_any_failure(self):
        # First call succeeds, rest fail
        responses = [_completed(stdout="OK set_hue ch=1 val=0 ret=0")] + \
                    [_completed(returncode=1, stderr="error")] * 17
        with patch("subprocess.run", side_effect=responses):
            result = adb_mod.reset_cms()
        assert result["ok"] is False


class TestGetAdbStatus:
    def test_connected_with_tool_deployed(self):
        with patch.object(adb_mod, "get_connected_devices", return_value=["tv:5555"]), \
             patch.object(adb_mod, "is_cms_tool_deployed", return_value=True):
            st = adb_mod.get_adb_status()
        assert st["connected"] is True
        assert st["cms_tool_deployed"] is True
        assert st["devices"] == ["tv:5555"]
        assert st["device"] == "tv:5555"

    def test_not_connected(self):
        with patch.object(adb_mod, "get_connected_devices", return_value=[]), \
             patch.object(adb_mod, "is_cms_tool_deployed", return_value=False):
            st = adb_mod.get_adb_status()
        assert st["connected"] is False
        assert st["cms_tool_deployed"] is False
        assert st["device"] is None

    def test_connected_tool_not_deployed(self):
        with patch.object(adb_mod, "get_connected_devices", return_value=["tv:5555"]), \
             patch.object(adb_mod, "is_cms_tool_deployed", return_value=False):
            st = adb_mod.get_adb_status()
        assert st["connected"] is True
        assert st["cms_tool_deployed"] is False


# ── Integration tests: /api/adb/* endpoints ───────────────────────────────────

class TestAdbStatusEndpoint:
    def test_returns_status_dict(self, client):
        with patch.object(adb_mod, "get_connected_devices", return_value=["tv:5555"]), \
             patch.object(adb_mod, "is_cms_tool_deployed", return_value=True):
            r = client.get("/api/adb/status")
        assert r.status_code == 200
        data = r.json()
        assert data["connected"] is True
        assert "devices" in data
        assert "cms_tool_deployed" in data

    def test_passes_device_query_param(self, client):
        with patch.object(adb_mod, "get_adb_status", return_value={
            "connected": True, "devices": ["tv:5555"],
            "cms_tool_deployed": True, "device": "tv:5555"
        }) as mock_status:
            r = client.get("/api/adb/status?device=tv%3A5555")
        assert r.status_code == 200
        mock_status.assert_called_once_with(device="tv:5555")


class TestAdbCmsPushEndpoint:
    def test_successful_push(self, client):
        with patch.object(adb_mod, "push_cms_tool",
                          return_value={"ok": True, "stdout": "1 file pushed", "stderr": ""}):
            r = client.post("/api/adb/cms/push")
        assert r.status_code == 200

    def test_failed_push_returns_502(self, client):
        with patch.object(adb_mod, "push_cms_tool",
                          return_value={"ok": False, "stdout": "", "stderr": "no devices"}):
            r = client.post("/api/adb/cms/push")
        assert r.status_code == 502


class TestAdbCmsSetEndpoint:
    def test_valid_request_returns_ok(self, client):
        with patch.object(adb_mod, "set_cms_value",
                          return_value={"ok": True, "stdout": "OK set_hue ch=1 val=3 ret=0", "stderr": ""}):
            r = client.post("/api/adb/cms/set",
                            json={"channel": "Red", "control": "Hue", "value": 3})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_invalid_channel_returns_400(self, client):
        r = client.post("/api/adb/cms/set",
                        json={"channel": "Purple", "control": "Hue", "value": 0})
        assert r.status_code == 400

    def test_invalid_control_returns_400(self, client):
        r = client.post("/api/adb/cms/set",
                        json={"channel": "Red", "control": "Tint", "value": 0})
        assert r.status_code == 400

    def test_out_of_range_value_returns_400(self, client):
        r = client.post("/api/adb/cms/set",
                        json={"channel": "Red", "control": "Hue", "value": 99})
        assert r.status_code == 400

    def test_adb_failure_returns_502(self, client):
        with patch.object(adb_mod, "set_cms_value",
                          return_value={"ok": False, "stdout": "", "stderr": "error: no devices"}):
            r = client.post("/api/adb/cms/set",
                            json={"channel": "Red", "control": "Hue", "value": 0})
        assert r.status_code == 502

    def test_all_channels_accepted(self, client):
        for ch in adb_mod.CMS_CHANNELS:
            with patch.object(adb_mod, "set_cms_value",
                              return_value={"ok": True, "stdout": "OK", "stderr": ""}):
                r = client.post("/api/adb/cms/set",
                                json={"channel": ch, "control": "Hue", "value": 0})
            assert r.status_code == 200, f"Failed for channel {ch}"

    def test_all_controls_accepted(self, client):
        for ctrl in adb_mod.CMS_SET_ACTIONS:
            with patch.object(adb_mod, "set_cms_value",
                              return_value={"ok": True, "stdout": "OK", "stderr": ""}):
                r = client.post("/api/adb/cms/set",
                                json={"channel": "Red", "control": ctrl, "value": 0})
            assert r.status_code == 200, f"Failed for control {ctrl}"


class TestAdbCmsGetEndpoint:
    def test_valid_get_returns_value(self, client):
        with patch.object(adb_mod, "get_cms_value",
                          return_value={"ok": True, "value": 5, "stdout": "VALUE 5", "stderr": ""}):
            r = client.post("/api/adb/cms/get",
                            json={"channel": "Red", "control": "Hue"})
        assert r.status_code == 200
        assert r.json()["value"] == 5

    def test_invalid_channel_returns_400(self, client):
        r = client.post("/api/adb/cms/get",
                        json={"channel": "Purple", "control": "Hue"})
        assert r.status_code == 400


class TestAdbCmsGetAllEndpoint:
    def test_returns_all_values(self, client):
        values = {c: {"hue": 0, "sat": 0, "bri": 0} for c in adb_mod.CMS_CHANNELS}
        with patch.object(adb_mod, "get_all_cms_values",
                          return_value={"ok": True, "values": values, "stdout": "", "stderr": ""}):
            r = client.get("/api/adb/cms/all")
        assert r.status_code == 200
        assert len(r.json()["values"]) == 6


class TestAdbCmsResetEndpoint:
    def test_valid_reset(self, client):
        with patch.object(adb_mod, "reset_cms",
                          return_value={"ok": True, "stdout": "", "stderr": ""}):
            r = client.post("/api/adb/cms/reset")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_adb_failure_returns_502(self, client):
        with patch.object(adb_mod, "reset_cms",
                          return_value={"ok": False, "stdout": "", "stderr": "no devices"}):
            r = client.post("/api/adb/cms/reset")
        assert r.status_code == 502


# ── Unit tests: set/get_picture_control ───────────────────────────────────────

class TestSetPictureControl:
    def test_valid_brightness_call(self):
        mock = _completed(stdout="OK set_brightness_main val=50 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            result = adb_mod.set_picture_control("Brightness", 50)
        assert result["ok"] is True
        shell_cmd = " ".join(run.call_args[0][0])
        assert "set_brightness_main" in shell_cmd
        assert "50" in shell_cmd

    def test_valid_contrast_call(self):
        mock = _completed(stdout="OK set_contrast val=75 ret=0")
        with patch("subprocess.run", return_value=mock) as run:
            adb_mod.set_picture_control("Contrast", 75)
        assert "set_contrast" in " ".join(run.call_args[0][0])

    def test_picture_mode_unconstrained(self):
        """PictureMode accepts values outside 0–100."""
        mock = _completed(stdout="OK set_picture_mode val=3 ret=0")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.set_picture_control("PictureMode", 3)
        assert result["ok"] is True

    def test_raises_on_out_of_range_for_brightness(self):
        with pytest.raises(ValueError, match="out of range"):
            adb_mod.set_picture_control("Brightness", 101)

    def test_raises_on_unknown_control(self):
        with pytest.raises(ValueError, match="Unknown control"):
            adb_mod.set_picture_control("Tint", 50)

    def test_boundary_values_accepted(self):
        for v in (0, 50, 100):
            mock = _completed(stdout=f"OK set_contrast val={v} ret=0")
            with patch("subprocess.run", return_value=mock):
                result = adb_mod.set_picture_control("Contrast", v)
            assert result["ok"] is True


class TestGetPictureControl:
    def test_parses_value_line(self):
        mock = _completed(stdout="VALUE 42\n")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.get_picture_control("Brightness")
        assert result["ok"] is True
        assert result["value"] == 42

    def test_raises_on_unknown_control(self):
        with pytest.raises(ValueError, match="Unknown control"):
            adb_mod.get_picture_control("Tint")

    def test_ok_false_when_no_value_line(self):
        mock = _completed(returncode=0, stdout="ERROR something\n")
        with patch("subprocess.run", return_value=mock):
            result = adb_mod.get_picture_control("Contrast")
        assert result["ok"] is False
        assert result["value"] is None


# ── Integration tests: /api/adb/picture/* endpoints ───────────────────────────

class TestAdbPictureSetEndpoint:
    def test_valid_set(self, client):
        with patch.object(adb_mod, "set_picture_control",
                          return_value={"ok": True, "stdout": "OK set_brightness_main val=50 ret=0", "stderr": ""}):
            r = client.post("/api/adb/picture/set", json={"control": "Brightness", "value": 50})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_unknown_control_returns_400(self, client):
        r = client.post("/api/adb/picture/set", json={"control": "Tint", "value": 50})
        assert r.status_code == 400

    def test_out_of_range_returns_400(self, client):
        r = client.post("/api/adb/picture/set", json={"control": "Brightness", "value": 200})
        assert r.status_code == 400

    def test_adb_failure_returns_502(self, client):
        with patch.object(adb_mod, "set_picture_control",
                          return_value={"ok": False, "stdout": "", "stderr": "no devices"}):
            r = client.post("/api/adb/picture/set", json={"control": "Contrast", "value": 50})
        assert r.status_code == 502

    def test_all_controls_accepted(self, client):
        for ctrl in adb_mod.PICTURE_SET_ACTIONS:
            with patch.object(adb_mod, "set_picture_control",
                              return_value={"ok": True, "stdout": "OK", "stderr": ""}):
                r = client.post("/api/adb/picture/set", json={"control": ctrl, "value": 50})
            assert r.status_code == 200, f"Failed for control {ctrl}"


class TestAdbPictureGetEndpoint:
    def test_valid_get(self, client):
        with patch.object(adb_mod, "get_picture_control",
                          return_value={"ok": True, "value": 42, "stdout": "VALUE 42", "stderr": ""}):
            r = client.post("/api/adb/picture/get", json={"control": "Brightness"})
        assert r.status_code == 200
        assert r.json()["value"] == 42

    def test_unknown_control_returns_400(self, client):
        r = client.post("/api/adb/picture/get", json={"control": "Tint"})
        assert r.status_code == 400

    def test_adb_failure_returns_502(self, client):
        with patch.object(adb_mod, "get_picture_control",
                          return_value={"ok": False, "value": None, "stdout": "", "stderr": "no devices"}):
            r = client.post("/api/adb/picture/get", json={"control": "Contrast"})
        assert r.status_code == 502
