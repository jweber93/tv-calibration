"""Tests for _step_quality quality gate evaluation (issue #95)."""
import pytest

from calibrator import Measurement, SDR_TARGET
from calibrator.profiles import TV_PROFILES
from server import _step_quality

TV = TV_PROFILES["u8g"]


def _base_session(**overrides):
    s = {
        "tv_key": "u8g",
        "target": SDR_TARGET,
        "peak_luminance": 0.0,
        "wb_measurements": [],
        "gamma_measurements": [],
        "cms_measurements": [],
        "pre_measurements": [],
        "post_measurements": [],
        "grayscale_ramp_steps": 11,
        "gamma_workflow": "quick",
    }
    s.update(overrides)
    return s


def _gray(label, Y, stim_rgb):
    return Measurement(
        x=0.3127, y=0.3290, Y=Y, X=1.0, Z=1.0,
        label=label, stimulus_rgb=stim_rgb,
        timestamp="2026-01-01T00:00:00",
    )


# ── Luminance ─────────────────────────────────────────────────────────────────

class TestLuminanceGate:
    def test_passes_when_within_5pct(self):
        s = _base_session(peak_luminance=118.0)  # 118/120 = 1.7% off
        gates = _step_quality(s, TV)
        assert "luminance" in gates
        assert gates["luminance"]["passed"] is True
        assert gates["luminance"]["score"] == pytest.approx(1.7, abs=0.1)

    def test_fails_when_outside_5pct(self):
        s = _base_session(peak_luminance=100.0)  # 100/120 = 16.7% off
        gates = _step_quality(s, TV)
        assert gates["luminance"]["passed"] is False

    def test_absent_when_no_measurement(self):
        s = _base_session(peak_luminance=0.0)
        gates = _step_quality(s, TV)
        assert "luminance" not in gates

    def test_absent_when_no_target(self):
        s = _base_session(peak_luminance=120.0, target=None)
        gates = _step_quality(s, TV)
        assert "luminance" not in gates

    def test_exactly_at_threshold_passes(self):
        s = _base_session(peak_luminance=120.0 * 0.95)  # exactly 5%
        gates = _step_quality(s, TV)
        assert gates["luminance"]["passed"] is True


# ── White Balance ─────────────────────────────────────────────────────────────

class TestWhiteBalanceGate:
    # On-target Y values for SDR (peak=120, gamma=2.2):
    #   80% stim → expected_Y = 120 * 0.80^2.2 ≈ 74.4 nits
    #   30% stim → expected_Y = 120 * 0.302^2.2 ≈ 9.1 nits
    def _gain_m(self, Y=74.4):
        return _gray("80% Gray", Y, (204, 204, 204))

    def _offset_m(self, Y=9.1):
        return _gray("30% Gray", Y, (77, 77, 77))

    def test_passes_when_both_de_under_threshold(self):
        # On-target D65 measurements should have near-zero ΔE
        s = _base_session(wb_measurements=[self._gain_m(), self._offset_m()])
        gates = _step_quality(s, TV)
        assert "white_balance" in gates
        assert gates["white_balance"]["passed"] is True

    def test_fails_when_only_gain_present(self):
        s = _base_session(wb_measurements=[self._gain_m()])
        gates = _step_quality(s, TV)
        assert gates["white_balance"]["passed"] is False

    def test_absent_when_no_measurements(self):
        s = _base_session(wb_measurements=[])
        gates = _step_quality(s, TV)
        assert "white_balance" not in gates


# ── Gamma ─────────────────────────────────────────────────────────────────────

class TestGammaGate:
    def _gamma_m(self, stim_pct, Y=None, label=None):
        # Build a measurement at the given stimulus, on-target for SDR gamma 2.2
        if Y is None:
            Y = SDR_TARGET.peak_luminance_nits * (stim_pct / 100.0) ** SDR_TARGET.gamma
        if label is None:
            label = f"Gamma {stim_pct}%"
        cv = round(16 + (stim_pct / 100) * (235 - 16))
        return _gray(label, Y, (cv, cv, cv))

    def test_passes_when_all_points_on_target(self):
        meas = [self._gamma_m(p) for p in (20, 40, 60, 80)]
        s = _base_session(gamma_measurements=meas)
        gates = _step_quality(s, TV)
        assert "gamma" in gates
        assert gates["gamma"]["passed"] is True

    def test_fails_when_point_deviates(self):
        meas = [self._gamma_m(p) for p in (20, 40, 60, 80)]
        # Replace 40% with Y value that gives gamma ~2.6 (way off 2.2)
        bad_Y = SDR_TARGET.peak_luminance_nits * 0.4 ** 2.6
        meas[1] = _gray("Gamma 40%", bad_Y, (100, 100, 100))
        s = _base_session(gamma_measurements=meas)
        gates = _step_quality(s, TV)
        assert gates["gamma"]["passed"] is False

    def test_absent_when_no_measurements(self):
        s = _base_session(gamma_measurements=[])
        gates = _step_quality(s, TV)
        assert "gamma" not in gates


# ── Color Tuner ───────────────────────────────────────────────────────────────

class TestColorTunerGate:
    def _colour_m(self, name, x, y, Y=40.0):
        from server import CMS_PATCHES as _patches
        rgb = _patches.get(name, (235, 16, 16))
        return Measurement(
            x=x, y=y, Y=Y, X=1.0, Z=1.0,
            label=f"{name} 100%", stimulus_rgb=rgb,
            timestamp="2026-01-01T00:00:00",
        )

    def test_passes_when_all_six_colors_under_threshold(self):
        # Use D65 coordinates for all — ΔE will be non-zero due to chromaticity
        # but we just need all 6 present and ΔE < 2. Use coords close to D65.
        colors = ["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"]
        meas = [self._colour_m(c, 0.3127, 0.3290, Y=40.0) for c in colors]
        s = _base_session(cms_measurements=meas)
        gates = _step_quality(s, TV)
        assert "color_tuner" in gates
        # All are at D65 chromaticity vs D65 target → ΔE ≈ 0 for all
        assert gates["color_tuner"]["passed"] is True

    def test_fails_when_not_all_colors_present(self):
        # Only 3 of 6 colors measured
        meas = [self._colour_m(c, 0.3127, 0.3290) for c in ["Red", "Green", "Blue"]]
        s = _base_session(cms_measurements=meas)
        gates = _step_quality(s, TV)
        assert gates["color_tuner"]["passed"] is False

    def test_absent_when_no_measurements(self):
        s = _base_session(cms_measurements=[])
        gates = _step_quality(s, TV)
        assert "color_tuner" not in gates


# ── Grayscale (completion) ────────────────────────────────────────────────────

class TestGrayscaleGates:
    def _ramp(self, n):
        from server import _grayscale_levels_for_ramp
        levels = _grayscale_levels_for_ramp(n)
        return [
            _gray(f"{p}% Gray", p / 100 * 120, (round(16 + p / 100 * 219),) * 3)
            for p in levels
        ]

    def test_pre_grayscale_complete(self):
        s = _base_session(pre_measurements=self._ramp(11))
        gates = _step_quality(s, TV)
        assert gates["pre_grayscale"]["passed"] is True
        assert gates["pre_grayscale"]["score"] == 11

    def test_pre_grayscale_incomplete(self):
        s = _base_session(pre_measurements=self._ramp(11)[:5])
        gates = _step_quality(s, TV)
        assert gates["pre_grayscale"]["passed"] is False
        assert gates["pre_grayscale"]["score"] == 5

    def test_post_grayscale_complete(self):
        s = _base_session(post_measurements=self._ramp(11))
        gates = _step_quality(s, TV)
        assert gates["post_grayscale"]["passed"] is True

    def test_absent_when_no_measurements(self):
        s = _base_session()
        gates = _step_quality(s, TV)
        assert "pre_grayscale" not in gates
        assert "post_grayscale" not in gates
