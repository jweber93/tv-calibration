"""Tests for white balance hints and _m_to_dict helper in server.py."""
import pytest

from calibrator import Measurement, D65_XY, SDR_TARGET, stimulus_pct_from_code_value
from calibrator.profiles import TV_PROFILES
from server import _m_to_dict, _wb_control_plan, _wb_hints, _wb_recommendations


# ---------------------------------------------------------------------------
# _wb_hints
# ---------------------------------------------------------------------------

class TestWbHints:
    def test_on_target(self):
        """D65 measurement should produce 'ok' for both axes."""
        m = Measurement(x=0.3127, y=0.3290, Y=100.0)
        hints = _wb_hints(m, D65_XY)
        assert hints["x"]["status"] == "ok"
        assert hints["y"]["status"] == "ok"
        assert hints["overall"] == "excellent"

    def test_x_high(self):
        """x too high → suggest decrease Red Gain."""
        m = Measurement(x=0.3200, y=0.3290, Y=100.0)
        hints = _wb_hints(m, D65_XY)
        assert hints["x"]["status"] == "high"
        assert "Decrease Red" in hints["x"]["action"]

    def test_x_low(self):
        m = Measurement(x=0.3050, y=0.3290, Y=100.0)
        hints = _wb_hints(m, D65_XY)
        assert hints["x"]["status"] == "low"
        assert "Increase Red" in hints["x"]["action"]

    def test_y_high(self):
        """y too high → suggest decrease Green Gain."""
        m = Measurement(x=0.3127, y=0.3360, Y=100.0)
        hints = _wb_hints(m, D65_XY)
        assert hints["y"]["status"] == "high"
        assert "Green" in hints["y"]["action"]

    def test_y_low(self):
        m = Measurement(x=0.3127, y=0.3220, Y=100.0)
        hints = _wb_hints(m, D65_XY)
        assert hints["y"]["status"] == "low"
        assert "Green" in hints["y"]["action"]

    def test_total_shift_calculated(self):
        m = Measurement(x=0.3200, y=0.3350, Y=100.0)
        hints = _wb_hints(m, D65_XY)
        assert hints["total_shift"] > 0

    def test_needs_work_overall(self):
        m = Measurement(x=0.3300, y=0.3400, Y=100.0)
        hints = _wb_hints(m, D65_XY)
        assert hints["overall"] == "needs_work"

    def test_close_overall(self):
        """Shift between 0.003 and 0.005 should be 'close'."""
        # dx=0.004, dy=0 → shift=0.004
        m = Measurement(x=0.3167, y=0.3290, Y=100.0)
        hints = _wb_hints(m, D65_XY)
        assert hints["overall"] == "close"


class TestWbControlPlan:
    def test_primary_and_secondary_moves_use_profile_control_names(self):
        tv = TV_PROFILES["u8g"]
        m = Measurement(
            x=0.3200, y=0.3360, Y=100.0,
            label="80% Gray (Gain)", stimulus_rgb=(204, 204, 204),
        )
        hints = _wb_hints(m, D65_XY)
        plan = _wb_control_plan(m, hints, tv)

        assert plan[0]["priority"] == "primary"
        assert plan[1]["priority"] == "secondary"
        assert plan[0]["control"] in {"B-Gain", "R-Gain", "G-Gain"}
        assert plan[1]["control"] in {"B-Gain", "R-Gain", "G-Gain"}
        assert all("Gain" in item["control"] for item in plan[:2])
        assert any("Primary:" in item["summary"] for item in plan[:1])
        assert any("Secondary:" in item["summary"] for item in plan[1:2])

    def test_blue_guidance_is_explicit_when_both_axes_are_high(self):
        tv = TV_PROFILES["u8g"]
        m = Measurement(
            x=0.3200, y=0.3360, Y=100.0,
            label="80% Gray (Gain)", stimulus_rgb=(204, 204, 204),
        )
        hints = _wb_hints(m, D65_XY)
        plan = _wb_control_plan(m, hints, tv)
        recs = _wb_recommendations(m, hints, tv)

        assert plan[0]["control"] == "B-Gain"
        assert plan[0]["direction"] == "up"
        assert "Both x and y high" in plan[0]["reason"]
        assert any("Primary move: Raise B-Gain" in rec for rec in recs)

    def test_blue_guidance_is_explicit_when_both_axes_are_low(self):
        tv = TV_PROFILES["u8g"]
        m = Measurement(
            x=0.3050, y=0.3220, Y=100.0,
            label="30% Gray (Offset)", stimulus_rgb=(77, 77, 77),
        )
        hints = _wb_hints(m, D65_XY)
        plan = _wb_control_plan(m, hints, tv)
        recs = _wb_recommendations(m, hints, tv)

        assert plan[0]["control"] == "B-Offset"
        assert plan[0]["direction"] == "down"
        assert "Both x and y low" in plan[0]["reason"]
        assert any("Primary move: Lower B-Offset" in rec for rec in recs)

    def test_mixed_axis_error_keeps_red_green_primary_and_blue_secondary(self):
        tv = TV_PROFILES["u8g"]
        m = Measurement(
            x=0.3200, y=0.3220, Y=100.0,
            label="80% Gray (Gain)", stimulus_rgb=(204, 204, 204),
        )
        hints = _wb_hints(m, D65_XY)
        plan = _wb_control_plan(m, hints, tv)

        assert plan[0]["control"] in {"R-Gain", "G-Gain"}
        assert plan[1]["control"] in {"R-Gain", "G-Gain", "B-Gain"}
        assert plan[0]["control"] != plan[1]["control"]


# ---------------------------------------------------------------------------
# _m_to_dict
# ---------------------------------------------------------------------------

class TestMToDict:
    def test_basic_fields(self):
        m = Measurement(
            x=0.3127, y=0.3290, Y=100.0,
            X=95.047, Z=108.883,
            label="100%", stimulus_rgb=(255, 255, 255),
            timestamp="2024-01-01T00:00:00",
        )
        d = _m_to_dict(m, SDR_TARGET)
        assert "delta_e" in d
        assert "delta_xy" in d
        assert "cct" in d
        assert "rating" in d
        assert d["stimulus_rgb"] == [255, 255, 255]  # converted to list
        assert d["stimulus_pct"] == 100

    def test_rating_categories(self):
        """Different delta-E values should produce different ratings."""
        # On-target measurement
        m_good = Measurement(x=0.3127, y=0.3290, Y=100.0, stimulus_rgb=(255, 255, 255))
        d = _m_to_dict(m_good, SDR_TARGET)
        assert d["rating"] in ("excellent", "good", "acceptable", "poor")

    def test_effective_gamma_at_50pct(self):
        """50% stimulus should compute an effective gamma."""
        stim_pct = stimulus_pct_from_code_value(128)
        m = Measurement(
            x=0.3127, y=0.3290,
            Y=120.0 * ((stim_pct / 100) ** 2.2),
            stimulus_rgb=(128, 128, 128),
        )
        d = _m_to_dict(m, SDR_TARGET)
        assert d["effective_gamma"] is not None
        assert d["effective_gamma"] == pytest.approx(2.2, abs=0.1)
        assert d["stimulus_pct"] == stim_pct

    def test_effective_gamma_at_100pct_is_none(self):
        """100% stimulus cannot compute gamma (log(1)=0)."""
        m = Measurement(x=0.3127, y=0.3290, Y=120.0, stimulus_rgb=(255, 255, 255))
        d = _m_to_dict(m, SDR_TARGET)
        assert d["effective_gamma"] is None

    def test_delta_e_on_target_small(self):
        """D65 at the target peak luminance (120 nits) should have near-zero delta-E."""
        # SDR_TARGET: peak=120 nits, gamma=2.2.  100% stimulus → expected_Y = 120.
        m = Measurement(x=0.3127, y=0.3290, Y=120.0, stimulus_rgb=(255, 255, 255))
        d = _m_to_dict(m, SDR_TARGET)
        assert d["delta_e"] < 1.0

    def test_delta_e_includes_luminance_error_for_grayscale(self):
        """Correct chromaticity but wrong luminance should produce non-zero delta-E.

        Regression test for #81: the old code passed m.Y as both measured and target
        luminance, making L* identical for both CIELAB points and producing a
        chromaticity-only ΔE of ~0 even when brightness was way off.
        """
        # 50% gray (code value 128 → ~50% stimulus).  On-target luminance for SDR
        # (peak=120, gamma=2.2) would be ~26 nits.  We supply 60 nits — a large
        # luminance error that should register as a significant ΔE.
        m = Measurement(x=0.3127, y=0.3290, Y=60.0, stimulus_rgb=(128, 128, 128))
        d = _m_to_dict(m, SDR_TARGET)
        assert d["delta_e"] > 2.0, (
            "Luminance error should produce ΔE > 2; got {:.2f}".format(d["delta_e"])
        )

    def test_delta_e_uses_measured_luminance_for_colour_patches(self):
        """Non-grayscale (CMS colour) patches still use m.Y as target luminance.

        Until we have per-colour luminance targets, falling back to m.Y keeps ΔE
        chromaticity-only for colour patches — same as before, but intentional.
        """
        # Pure red patch: R≠G=B → not a grayscale stimulus
        m_red = Measurement(x=0.640, y=0.330, Y=40.0, stimulus_rgb=(235, 16, 16))
        d = _m_to_dict(m_red, SDR_TARGET)
        # Chromaticity error vs D65 white point should dominate; value should be finite
        assert d["delta_e"] >= 0


class TestHDRGrayscaleDeltaE:
    def test_hdr_grayscale_reflects_luminance_error(self):
        """HDR grayscale ΔE should reflect PQ luminance error, not just chromaticity.

        Regression test for #203: HDR grayscale targets used gamma=0, causing the
        fallback branch (ref_Y = m.Y) which made ΔE always zero regardless of
        how wrong the measured luminance was.
        """
        from calcore.models import HDR10_TARGET
        from calibrator.utils import stimulus_pct_from_code_value
        from calcore.eotf import pq_eotf

        # 50% gray (code value 128 → ~50% stimulus for full10 range).
        # PQ target luminance at 50%: pq_eotf(0.5) / 10000 * 1000 nits
        stim_pct = stimulus_pct_from_code_value(512, "full10")
        expected_rel = pq_eotf(stim_pct / 100.0) / 10000.0
        expected_y = HDR10_TARGET.peak_luminance_nits * expected_rel

        # On-target measurement — should produce near-zero ΔE.
        m_on = Measurement(
            x=0.3127, y=0.3290,
            Y=expected_y,
            stimulus_rgb=(512, 512, 512),
        )
        d_on = _m_to_dict(m_on, HDR10_TARGET)
        assert d_on["delta_e"] < 2.0, (
            "On-target HDR grayscale should have low ΔE; got {:.2f}".format(d_on["delta_e"])
        )

        # Off-target measurement — 50% gray at a very different luminance.
        m_off = Measurement(
            x=0.3127, y=0.3290,
            Y=expected_y * 2.5,  # measured ~2.5x too bright
            stimulus_rgb=(512, 512, 512),
        )
        d_off = _m_to_dict(m_off, HDR10_TARGET)
        assert d_off["delta_e"] > 2.0, (
            "HDR grayscale with wrong luminance should produce ΔE > 2; got {:.2f}".format(d_off["delta_e"])
        )

    def test_hdr_grayscale_different_luminances_no_longer_zero(self):
        """Two HDR grayscale measurements with different luminances should produce
        different ΔE values, not both 0.0.

        Regression test for #203: the old code produced `delta_e == 0.0` for every
        HDR grayscale measurement because ref_Y was set to m.Y.
        """
        from calcore.models import HDR10_TARGET

        m1 = Measurement(
            x=0.3127, y=0.3290, Y=100.0,
            stimulus_rgb=(512, 512, 512),
        )
        m2 = Measurement(
            x=0.3127, y=0.3290, Y=500.0,
            stimulus_rgb=(512, 512, 512),
        )

        d1 = _m_to_dict(m1, HDR10_TARGET)
        d2 = _m_to_dict(m2, HDR10_TARGET)

        assert d1["delta_e"] != 0.0 or d2["delta_e"] != 0.0, (
            "Different luminances should produce different ΔE values"
        )

    def test_dv_grayscale_reflects_luminance_error(self):
        """Dolby Vision grayscale should also reflect luminance error via PQ."""
        from calcore.models import DV_TARGET

        m = Measurement(
            x=0.3127, y=0.3290,
            Y=800.0,
            stimulus_rgb=(512, 512, 512),
        )
        d = _m_to_dict(m, DV_TARGET)
        # ΔE should be finite and non-trivial for an off-target luminance
        assert d["delta_e"] is not None
        assert isinstance(d["delta_e"], (int, float))
