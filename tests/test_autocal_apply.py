"""Tests for calibrator/autocal_apply.py (Autocal roadmap Item 1a)."""
from unittest.mock import patch

import pytest

from calcore.models import Measurement, Patch
from calibrator.autocal import CorrectionResult
from calibrator.autocal_apply import (
    AdbApplyTarget,
    CallableMeasurementSource,
    ManualApplyTarget,
)


def _correction(step=1, new_value=1, control="Hue", reason="damped step"):
    return CorrectionResult(
        control=control,
        step=step,
        new_value=new_value,
        error=0.05,
        damping=0.6,
        gain=0.01,
        gain_source="assumed",
        converged=step == 0,
        oscillating=False,
        stalled=False,
        out_of_range=False,
        reason=reason,
    )


class TestCallableMeasurementSource:
    def test_delegates_to_trigger_ignoring_patch(self):
        expected = Measurement(x=0.3, y=0.3, Y=50.0, label="Red")
        source = CallableMeasurementSource(lambda: expected)
        patch_obj = Patch(label="Red", r_target=940, g_target=0, b_target=0, meas_xyz=(0, 0, 0))
        assert source.measure(patch_obj) is expected


class TestManualApplyTarget:
    def test_no_change_needed_when_step_is_zero(self):
        result = ManualApplyTarget().apply(_correction(step=0, new_value=3, reason="within deadband"), colour="Red")
        assert result.ok is True
        assert result.requires_user_action is False
        assert "no change needed" in result.message.lower()

    def test_describes_raise_and_lower(self):
        up = ManualApplyTarget().apply(_correction(step=2, new_value=5), colour="Red")
        assert up.requires_user_action is True
        assert "Raise Hue to 5" in up.message

        down = ManualApplyTarget().apply(_correction(step=-2, new_value=-5), colour="Green")
        assert "Lower Hue to -5" in down.message

    def test_never_touches_the_tv(self):
        # No subprocess/adb import at all in this module's dependency graph;
        # applying never raises even without ADB present.
        result = ManualApplyTarget().apply(_correction(), colour="Blue")
        assert result.ok is True


class TestAdbApplyTarget:
    def test_unknown_colour_rejected(self):
        result = AdbApplyTarget().apply(_correction(), colour="Purple")
        assert result.ok is False
        assert "Unknown CMS colour" in result.message

    def test_no_change_needed_skips_adb_calls(self):
        with patch("calibrator.adb_control.set_cms_value") as set_mock:
            result = AdbApplyTarget().apply(_correction(step=0, new_value=0), colour="Red")
        assert result.ok is True
        set_mock.assert_not_called()

    def test_applies_and_verifies_readback(self):
        with patch("calibrator.adb_control.set_cms_value", return_value={"ok": True, "stdout": "OK", "stderr": ""}) as set_mock, \
             patch("calibrator.adb_control.get_cms_value", return_value={"ok": True, "value": 5, "stdout": "VALUE 5", "stderr": ""}) as get_mock:
            result = AdbApplyTarget(device="serial123").apply(_correction(step=2, new_value=5), colour="Red")
        assert result.ok is True
        assert "verified" in result.message
        set_mock.assert_called_once_with("Red", "Hue", 5, device="serial123")
        get_mock.assert_called_once_with("Red", "Hue", device="serial123")

    def test_set_failure_reported(self):
        with patch("calibrator.adb_control.set_cms_value", return_value={"ok": False, "stdout": "", "stderr": "device offline"}):
            result = AdbApplyTarget().apply(_correction(step=2, new_value=5), colour="Red")
        assert result.ok is False
        assert "ADB set failed" in result.message

    def test_readback_mismatch_reported(self):
        with patch("calibrator.adb_control.set_cms_value", return_value={"ok": True, "stdout": "OK", "stderr": ""}), \
             patch("calibrator.adb_control.get_cms_value", return_value={"ok": True, "value": 4, "stdout": "VALUE 4", "stderr": ""}):
            result = AdbApplyTarget().apply(_correction(step=2, new_value=5), colour="Red")
        assert result.ok is False
        assert "Read-back mismatch" in result.message

    def test_invalid_control_raises_are_caught(self):
        bad = _correction(control="NotAControl", step=1, new_value=1)
        with patch("calibrator.adb_control.set_cms_value", side_effect=ValueError("Unknown control")):
            result = AdbApplyTarget().apply(bad, colour="Red")
        assert result.ok is False
        assert "Unknown control" in result.message
