"""Tests for calibrator.csv_adapter — generic CSV patch bucket routing."""
from __future__ import annotations

import pytest

from calcore.models import Patch, CalibrationTarget, CalMode
from calibrator.csv_adapter import (
    patches_to_session_buckets,
    _nearest_target_pct,
    _stimulus_pct_from_patch,
    _bucket_for_grayscale,
    WB_GAIN_TARGET_PCT,
    WB_OFFSET_TARGET_PCT,
    GAMMA_TARGET_PCTS,
)

SDR_TARGET = CalibrationTarget(
    mode=CalMode.SDR,
    gamma=2.2,
    peak_luminance_nits=120.0,
    gamut="bt709",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gray_patch(label: str, code_value: int) -> Patch:
    """Create a grayscale patch at the given 8-bit code value."""
    return Patch(
        label=label,
        r_target=code_value,
        g_target=code_value,
        b_target=code_value,
        meas_xyz=(float(code_value), float(code_value) * 1.2, float(code_value)),
        meas_yxy=(float(code_value) * 1.2, 0.3127, 0.3290),
        kind="grayscale",
    )


def _color_patch(label: str, r: int, g: int, b: int) -> Patch:
    return Patch(
        label=label,
        r_target=r,
        g_target=g,
        b_target=b,
        meas_xyz=(10.0, 12.0, 8.0),
        meas_yxy=(12.0, 0.45, 0.40),
        kind="color",
    )


# ---------------------------------------------------------------------------
# _nearest_target_pct
# ---------------------------------------------------------------------------
class TestNearestTargetPct:
    def test_exact_match(self):
        assert _nearest_target_pct(50.0, (45.0, 50.0, 55.0)) == 50.0

    def test_no_match_outside_tolerance(self):
        assert _nearest_target_pct(7.0, (30.0, 80.0)) is None

    def test_nearest_within_tolerance(self):
        # 75% is within 7 of 80
        result = _nearest_target_pct(75.0, (30.0, 80.0))
        assert result == 80.0

    def test_empty_targets(self):
        assert _nearest_target_pct(50.0, ()) is None

    def test_multiple_matches_returns_closest(self):
        # 37% is within tolerance of both 30 and some other target
        result = _nearest_target_pct(37.0, (30.0, 45.0))
        assert result == 30.0


# ---------------------------------------------------------------------------
# _stimulus_pct_from_patch
# ---------------------------------------------------------------------------
class TestStimulusPctFromPatch:
    def test_grayscale_returns_pct(self):
        p = _gray_patch("50% Gray", 128)
        pct = _stimulus_pct_from_patch(p)
        assert pct > 0

    def test_color_returns_zero(self):
        p = _color_patch("Red", 255, 0, 0)
        assert _stimulus_pct_from_patch(p) == 0.0

    def test_black_returns_zero(self):
        p = _gray_patch("Black", 16)
        assert _stimulus_pct_from_patch(p) == 0.0

    def test_white_returns_100(self):
        p = _gray_patch("White", 235)
        assert _stimulus_pct_from_patch(p) >= 99.0


# ---------------------------------------------------------------------------
# _bucket_for_grayscale
# ---------------------------------------------------------------------------
class TestBucketForGrayscale:
    def test_black_no_session_step(self):
        # 0% gray, no session step: lum_measurements is reserved for
        # near-peak-white readings (#655) — black snaps to the nearest
        # gamma target (5%) instead, and never lands in lum_measurements.
        buckets = _bucket_for_grayscale(0.0, None)
        assert "lum_measurements" not in buckets
        assert "gamma_measurements" in buckets

    def test_white_no_session_step(self):
        # 100% gray → lum_measurements
        buckets = _bucket_for_grayscale(100.0, None)
        assert "lum_measurements" in buckets

    def test_80_pct_gains_wb(self):
        buckets = _bucket_for_grayscale(80.0, None)
        assert "wb_measurements" in buckets

    def test_30_pct_gains_wb(self):
        buckets = _bucket_for_grayscale(30.0, None)
        assert "wb_measurements" in buckets

    def test_50_pct_gains_gamma(self):
        buckets = _bucket_for_grayscale(50.0, None)
        assert "gamma_measurements" in buckets

    def test_pre_step(self):
        buckets = _bucket_for_grayscale(50.0, "pre_grayscale")
        assert "pre_measurements" in buckets

    def test_post_step(self):
        buckets = _bucket_for_grayscale(50.0, "post_grayscale")
        assert "post_measurements" in buckets

    def test_multi_bucket_80_pct(self):
        # 80% gray should hit wb_measurements AND gamma_measurements
        buckets = _bucket_for_grayscale(80.0, None)
        assert "wb_measurements" in buckets
        assert "gamma_measurements" in buckets

    def test_multi_bucket_100_pct(self):
        # 100% white should hit lum_measurements
        buckets = _bucket_for_grayscale(100.0, None)
        assert "lum_measurements" in buckets


# ---------------------------------------------------------------------------
# patches_to_session_buckets — color patches
# ---------------------------------------------------------------------------
class TestColorPatches:
    def test_color_goes_to_cms(self):
        patches = [_color_patch("Red", 255, 0, 0)]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["cms_measurements"]) == 1
        assert result["lum_measurements"] == []
        assert result["wb_measurements"] == []

    def test_multiple_color_patches(self):
        patches = [
            _color_patch("Red", 255, 0, 0),
            _color_patch("Green", 0, 255, 0),
            _color_patch("Blue", 0, 0, 255),
        ]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["cms_measurements"]) == 3

    def test_color_preserves_measurement_data(self):
        patches = [_color_patch("Red", 255, 0, 0)]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        meas = result["cms_measurements"][0]
        assert meas["label"] == "Red"
        assert meas["stimulus_rgb"] == [255, 0, 0]
        assert "X" in meas
        assert "Y" in meas
        assert "Z" in meas
        assert "x" in meas
        assert "y" in meas
        assert "timestamp" in meas


# ---------------------------------------------------------------------------
# patches_to_session_buckets — grayscale no session step (backward compat)
# ---------------------------------------------------------------------------
class TestGrayscaleNoStep:
    def test_50_percent_gray(self):
        """Mid-ramp grayscale without a session step must NOT land in
        lum_measurements (#655) — only a near-peak-white reading may."""
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["lum_measurements"]) == 0
        # Should populate gamma since 50% is a gamma target
        assert len(result["gamma_measurements"]) == 1

    def test_80_percent_gray_gains_wb(self):
        """80% gray should populate wb_measurements."""
        patches = [_gray_patch("WB Gain", 204)]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["wb_measurements"]) == 1
        wb = result["wb_measurements"][0]
        assert wb["stimulus_rgb"] == [204, 204, 204]

    def test_30_percent_gray_gains_wb(self):
        """30% gray should populate wb_measurements."""
        patches = [_gray_patch("WB Offset", 77)]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["wb_measurements"]) == 1

    def test_100_percent_gray_gains_lum(self):
        """100% white should populate lum_measurements."""
        patches = [_gray_patch("White", 235)]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["lum_measurements"]) == 1

    def test_0_percent_gray(self):
        """Black is not a peak-white reading — it must not land in
        lum_measurements (#655); it snaps to the nearest gamma target."""
        patches = [_gray_patch("Black", 16)]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["lum_measurements"]) == 0
        assert len(result["gamma_measurements"]) == 1

    def test_gamma_steps_populate_gamma_bucket(self):
        """5%, 10%, ..., 95% should all land in gamma_measurements."""
        patches = [_gray_patch(f"{p}% Gray", int(p * 255 / 100)) for p in range(5, 100, 5)]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["gamma_measurements"]) == 19

    def test_mixed_color_and_grayscale(self):
        patches = [
            _gray_patch("50% Gray", 128),
            _color_patch("Red", 255, 0, 0),
            _gray_patch("White", 235),
        ]
        result = patches_to_session_buckets(patches, SDR_TARGET)
        assert len(result["cms_measurements"]) == 1
        assert len(result["lum_measurements"]) >= 1


# ---------------------------------------------------------------------------
# patches_to_session_buckets — grayscale with pre_grayscale step
# ---------------------------------------------------------------------------
class TestGrayscalePreStep:
    def test_pre_grayscale_routes_to_pre(self):
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "pre_grayscale")
        assert len(result["pre_measurements"]) == 1
        meas = result["pre_measurements"][0]
        assert meas["stimulus_rgb"] == [128, 128, 128]

    def test_pre_step_still_gains_wb(self):
        patches = [_gray_patch("WB Gain", 204)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "pre_grayscale")
        assert len(result["wb_measurements"]) == 1
        # Also in pre_measurements
        assert len(result["pre_measurements"]) == 1

    def test_pre_step_still_gains_gamma(self):
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "pre_grayscale")
        assert len(result["gamma_measurements"]) == 1
        assert len(result["pre_measurements"]) == 1

    def test_pre_step_100_pct_gains_lum(self):
        patches = [_gray_patch("White", 235)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "pre_grayscale")
        assert len(result["lum_measurements"]) == 1
        # Also in pre_measurements as primary grayscale bucket
        assert len(result["pre_measurements"]) == 1

    def test_multiple_pre_patches(self):
        patches = [
            _gray_patch("Black", 16),
            _gray_patch("50% Gray", 128),
            _gray_patch("White", 235),
        ]
        result = patches_to_session_buckets(patches, SDR_TARGET, "pre_grayscale")
        assert len(result["pre_measurements"]) == 3


# ---------------------------------------------------------------------------
# patches_to_session_buckets — grayscale with post_grayscale step
# ---------------------------------------------------------------------------
class TestGrayscalePostStep:
    def test_post_grayscale_routes_to_post(self):
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "post_grayscale")
        assert len(result["post_measurements"]) == 1
        meas = result["post_measurements"][0]
        assert meas["stimulus_rgb"] == [128, 128, 128]

    def test_post_step_still_gains_wb(self):
        patches = [_gray_patch("WB Gain", 204)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "post_grayscale")
        assert len(result["wb_measurements"]) == 1
        assert len(result["post_measurements"]) == 1

    def test_post_step_still_gains_gamma(self):
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "post_grayscale")
        assert len(result["gamma_measurements"]) == 1
        assert len(result["post_measurements"]) == 1

    def test_post_step_100_pct_gains_lum(self):
        patches = [_gray_patch("White", 235)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "post_grayscale")
        assert len(result["lum_measurements"]) == 1
        assert len(result["post_measurements"]) == 1


# ---------------------------------------------------------------------------
# patches_to_session_buckets — edge cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_empty_patches(self):
        result = patches_to_session_buckets([], SDR_TARGET)
        for bucket in result:
            assert len(result[bucket]) == 0

    def test_patch_without_yxy(self):
        p = Patch(
            label="Test",
            r_target=128,
            g_target=128,
            b_target=128,
            meas_xyz=(10.0, 12.0, 8.0),
            meas_yxy=None,
            kind="grayscale",
        )
        # 50% gray, no session step: routed to gamma_measurements, not
        # lum_measurements (#655) — see TestGrayscaleNoStep.test_50_percent_gray.
        result = patches_to_session_buckets([p], SDR_TARGET)
        meas = result["gamma_measurements"][0]
        # x, y should be computed from X, Y, Z
        assert meas["x"] == pytest.approx(10.0 / 30.0)
        assert meas["y"] == pytest.approx(12.0 / 30.0)

    def test_patch_with_yxy(self):
        p = Patch(
            label="Test",
            r_target=128,
            g_target=128,
            b_target=128,
            meas_xyz=(10.0, 12.0, 8.0),
            meas_yxy=(15.0, 0.32, 0.33),
            kind="grayscale",
        )
        # 50% gray, no session step: routed to gamma_measurements, not
        # lum_measurements (#655) — see TestGrayscaleNoStep.test_50_percent_gray.
        result = patches_to_session_buckets([p], SDR_TARGET)
        meas = result["gamma_measurements"][0]
        assert meas["Y"] == 15.0
        assert meas["x"] == pytest.approx(0.32)
        assert meas["y"] == pytest.approx(0.33)

    def test_all_buckets_initialized(self):
        result = patches_to_session_buckets([], SDR_TARGET)
        expected = {
            "pre_measurements", "cms_measurements", "lum_measurements",
            "wb_measurements", "gamma_measurements", "post_measurements",
        }
        assert set(result.keys()) == expected

    def test_preparation_step_routes_to_pre(self):
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "prepare")
        assert len(result["pre_measurements"]) == 1

    def test_select_mode_step_routes_to_pre(self):
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "select_mode")
        assert len(result["pre_measurements"]) == 1

    def test_suggested_patches_routes_to_post(self):
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "suggested_patches")
        assert len(result["post_measurements"]) == 1

    def test_report_step_routes_to_post(self):
        patches = [_gray_patch("50% Gray", 128)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "report")
        assert len(result["post_measurements"]) == 1

    def test_10bit_full_range_recontextualization(self):
        # Test that 10-bit full-range sessions properly recontextualize stimulus percentages
        # For 10-bit full-range, code values 0-1023 map to signal range [0, 1]
        # 80% stimulus should map to grayscale bucket (wb_measurements)
        patches = [
            _gray_patch("80% Gray", int(0.8 * 1023)),  # ~818 for 10-bit
            _gray_patch("30% Gray", int(0.3 * 1023)),  # ~307 for 10-bit
        ]
        # Without recontextualization (8-bit default): these would map incorrectly
        # With recontextualization (full10): they should map to wb_measurements
        result = patches_to_session_buckets(
            patches, SDR_TARGET, "gamma", signal_range="full", code_scale="10bit"
        )
        # Both should be in wb_measurements because they snap to 80% and 30% targets
        assert len(result["wb_measurements"]) >= 2, "10-bit full-range should route 80%/30% to wb_measurements"
        assert len(result["gamma_measurements"]) >= 2, "80% should also be in gamma (5-95% snapping)"


# ---------------------------------------------------------------------------
# #655 — mid-workflow grayscale must not corrupt peak_luminance
# ---------------------------------------------------------------------------
class TestMidWorkflowGrayscaleNotRoutedToLum:
    def test_mid_workflow_grayscale_not_routed_to_lum(self):
        """A 30%/80% gray pair imported at white_balance must not be treated
        as luminance (peak-white) readings — only wb/gamma."""
        patches = [_gray_patch("WB Offset", 77), _gray_patch("WB Gain", 204)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "white_balance")
        assert result["lum_measurements"] == []
        assert len(result["wb_measurements"]) == 2

    def test_mid_workflow_near_white_still_routes_to_lum(self):
        """A genuine ~100% white reading at a mid-workflow step is still a
        luminance measurement."""
        patches = [_gray_patch("White", 235)]
        result = patches_to_session_buckets(patches, SDR_TARGET, "white_balance")
        assert len(result["lum_measurements"]) == 1
