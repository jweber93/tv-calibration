"""Regression test for #606.

`_maybe_trigger_llm` must forward the session's target white point through to
`_run_llm_background` -> `analyze()`, mirroring every other `_calcore_analyze`
call site (`/gamut/diagnosis`, `/gamut/advise`, `/suggested-patches`,
`/next-settings`). Before the fix, the LLM background path always analyzed
against the D65 default regardless of the session's actual target, so a
non-D65 target would silently produce a different Summary (and therefore a
different adjustment plan) than every diagnostic endpoint in the same
session.
"""

import unittest
from unittest.mock import patch

from calcore.colour import D65_xy
from calcore.models import CalibrationTarget, CalMode, Measurement

import server

# D50 white point (x=0.3457, y=0.3585) — same fixture used in test_white_point.py
D50_xy = (0.3457, 0.3585)


class _SyncThread:
    """Stand-in for threading.Thread that runs its target synchronously.

    Lets the test observe the Summary handed to `_call_llm` without racing a
    real background thread.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _wb_measurement(pct: int, meas_xyz=(50.0, 50.0, 50.0)) -> Measurement:
    v = round(pct / 100 * 255)
    return Measurement(
        X=meas_xyz[0], Y=meas_xyz[1], Z=meas_xyz[2],
        label=f"{pct}%", stimulus_rgb=(v, v, v),
    )


def _session(white_point_xy):
    return {
        "id": "test-sid",
        "step": "baseline",
        "llm_config": {"endpoint": "http://localhost:1234", "model": "test-model"},
        "target": CalibrationTarget(mode=CalMode.SDR, white_point_xy=white_point_xy),
        "wb_measurements": [_wb_measurement(50)],
    }


class TestMaybeTriggerLlmWhitePoint(unittest.TestCase):
    """The LLM background analysis must honor the session's target white point."""

    def _trigger_and_capture_summary(self, session):
        captured = []

        def fake_call_llm(summary, *args, **kwargs):
            captured.append(summary)
            return ""

        with patch.object(server, "threading") as mock_threading:
            mock_threading.Thread.side_effect = _SyncThread
            with patch.object(server, "_call_llm", side_effect=fake_call_llm):
                server._maybe_trigger_llm("test-sid", session)

        self.assertEqual(len(captured), 1, "expected exactly one LLM call")
        return captured[0]

    def test_d50_target_produces_different_summary_than_d65(self):
        summary_d50 = self._trigger_and_capture_summary(_session(D50_xy))
        summary_d65 = self._trigger_and_capture_summary(_session(D65_xy))

        de_d50 = summary_d50.grayscale_rows[0]["dE2000"]
        de_d65 = summary_d65.grayscale_rows[0]["dE2000"]
        self.assertNotAlmostEqual(de_d50, de_d65, places=3)

    def test_d65_target_matches_default_analysis(self):
        """Sanity check: a D65 session target should match the D65 default."""
        from calcore.analysis import analyze
        from calcore.models import AnalysisConfig, Patch

        summary_via_trigger = self._trigger_and_capture_summary(_session(D65_xy))

        patch_obj = Patch(
            label="50%", r_target=128, g_target=128, b_target=128,
            meas_xyz=(50.0, 50.0, 50.0), kind="grayscale",
        )
        cfg = server._session_to_analysis_config(_session(D65_xy))
        summary_direct = analyze([patch_obj], cfg)

        de_trigger = summary_via_trigger.grayscale_rows[0]["dE2000"]
        de_direct = summary_direct.grayscale_rows[0]["dE2000"]
        self.assertAlmostEqual(de_trigger, de_direct, places=6)


if __name__ == "__main__":
    unittest.main()
