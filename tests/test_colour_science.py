"""Tests for colour science pure functions in calibrator.py."""
import ast
from pathlib import Path

import pytest

from calibrator import (
    xyY_to_XYZ,
    XYZ_to_lab,
    xyY_to_lab,
    delta_e_cie76,
    delta_xy,
    gamma_from_luminance,
    rating_emoji,
    direction_hint,
    D65_XY,
    D65_XYZ,
)
from calibrator.colour import Measurement


def test_colour_module_does_not_call_ensure_packages_at_import_time():
    source = Path("calibrator/colour.py").read_text()
    tree = ast.parse(source)

    top_level_calls = [
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]

    assert "ensure_packages" not in top_level_calls


# ---------------------------------------------------------------------------
# xyY ↔ XYZ conversion
# ---------------------------------------------------------------------------

class TestXyYToXYZ:
    def test_d65_white(self):
        """D65 white point at 100 nits should produce known XYZ."""
        X, Y, Z = xyY_to_XYZ(0.3127, 0.3290, 100.0)
        assert Y == 100.0
        assert X == pytest.approx(95.0456, rel=1e-3)
        assert Z == pytest.approx(108.875, rel=1e-2)

    def test_zero_luminance(self):
        X, Y, Z = xyY_to_XYZ(0.3127, 0.3290, 0.0)
        assert (X, Y, Z) == (0.0, 0.0, 0.0)

    def test_zero_y_chromaticity(self):
        """y=0 should return zeros to avoid division by zero."""
        assert xyY_to_XYZ(0.3, 0.0, 50.0) == (0, 0, 0)

    def test_pure_red_chromaticity(self):
        """Rec.709 red primary chromaticity at 10 nits."""
        X, Y, Z = xyY_to_XYZ(0.64, 0.33, 10.0)
        assert X == pytest.approx(19.394, rel=1e-2)
        assert Y == 10.0
        assert Z == pytest.approx(0.909, rel=1e-1)


# ---------------------------------------------------------------------------
# XYZ → CIELAB
# ---------------------------------------------------------------------------

class TestXYZToLab:
    def test_d65_gives_L100(self):
        """D65 reference white should give L*=100, a*=0, b*=0."""
        L, a, b = XYZ_to_lab(*D65_XYZ)
        assert L == pytest.approx(100.0, abs=0.01)
        assert a == pytest.approx(0.0, abs=0.01)
        assert b == pytest.approx(0.0, abs=0.01)

    def test_black(self):
        L, a, b = XYZ_to_lab(0, 0, 0)
        assert L == pytest.approx(0.0, abs=1.0)

    def test_known_value(self):
        """A known XYZ→Lab conversion (roughly a medium grey).
        XYZ_to_lab divides by ref_white, so we need to pass values
        already relative to D65 (i.e. Y/Yn ≈ 0.5 for mid-grey)."""
        # D65 reference XYZ scaled to 50% luminance
        X = D65_XYZ[0] * 0.5
        Y_val = D65_XYZ[1] * 0.5
        Z = D65_XYZ[2] * 0.5
        L, a, b = XYZ_to_lab(X, Y_val, Z)
        # L* for Y/Yn = 0.5 ≈ 76.07
        assert L == pytest.approx(76.07, abs=0.5)
        assert abs(a) < 1.0  # neutral grey, a* should be near 0
        assert abs(b) < 1.0


# ---------------------------------------------------------------------------
# xyY → Lab (normalised)
# ---------------------------------------------------------------------------

class TestXyYToLab:
    def test_normalised_d65_at_ref(self):
        """D65 at ref_nits should give L*=100."""
        L, a, b = xyY_to_lab(0.3127, 0.3290, 100.0, ref_nits=100.0)
        assert L == pytest.approx(100.0, abs=0.5)

    def test_half_luminance(self):
        """Half of ref luminance should give L* ≈ 76."""
        L, a, b = xyY_to_lab(0.3127, 0.3290, 50.0, ref_nits=100.0)
        assert L == pytest.approx(76.07, abs=0.5)


# ---------------------------------------------------------------------------
# Delta-E (CIE76)
# ---------------------------------------------------------------------------

class TestDeltaE:
    def test_identical_points(self):
        de = delta_e_cie76(0.3127, 0.3290, 100.0, 0.3127, 0.3290, 100.0)
        assert de == pytest.approx(0.0, abs=0.01)

    def test_symmetry(self):
        de1 = delta_e_cie76(0.31, 0.33, 80.0, 0.33, 0.34, 90.0)
        de2 = delta_e_cie76(0.33, 0.34, 90.0, 0.31, 0.33, 80.0)
        assert de1 == pytest.approx(de2, abs=0.001)

    def test_nonzero_for_different_points(self):
        de = delta_e_cie76(0.31, 0.33, 100.0, 0.33, 0.34, 100.0)
        assert de > 0

    def test_large_difference(self):
        """Red vs blue should produce a large delta-E."""
        de = delta_e_cie76(0.64, 0.33, 50.0, 0.15, 0.06, 50.0)
        assert de > 50


# ---------------------------------------------------------------------------
# delta_xy
# ---------------------------------------------------------------------------

class TestDeltaXy:
    def test_zero_distance(self):
        assert delta_xy(D65_XY, D65_XY) == 0.0

    def test_known_distance(self):
        assert delta_xy((0.0, 0.0), (3.0, 4.0)) == pytest.approx(5.0)

    def test_symmetry(self):
        d1 = delta_xy((0.31, 0.33), (0.35, 0.34))
        d2 = delta_xy((0.35, 0.34), (0.31, 0.33))
        assert d1 == pytest.approx(d2)


# ---------------------------------------------------------------------------
# gamma_from_luminance
# ---------------------------------------------------------------------------

class TestGammaFromLuminance:
    def test_perfect_22_gamma(self):
        """50% stimulus at gamma 2.2 should produce (0.5^2.2)*peak nits."""
        peak = 120.0
        stimulus = 50
        measured = peak * (stimulus / 100.0) ** 2.2
        g = gamma_from_luminance(measured, peak, stimulus)
        assert g == pytest.approx(2.2, abs=0.01)

    def test_perfect_24_gamma(self):
        peak = 100.0
        stimulus = 75
        measured = peak * (stimulus / 100.0) ** 2.4
        g = gamma_from_luminance(measured, peak, stimulus)
        assert g == pytest.approx(2.4, abs=0.01)

    def test_zero_stimulus_returns_none(self):
        assert gamma_from_luminance(50, 120, 0) is None

    def test_100_stimulus_returns_none(self):
        assert gamma_from_luminance(120, 120, 100) is None

    def test_zero_peak_returns_none(self):
        assert gamma_from_luminance(50, 0, 50) is None

    def test_negative_luminance_returns_none(self):
        assert gamma_from_luminance(-10, 120, 50) is None

    def test_above_peak_luminance_returns_none(self):
        assert gamma_from_luminance(130, 120, 50) is None


# ---------------------------------------------------------------------------
# rating_emoji
# ---------------------------------------------------------------------------

class TestRatingEmoji:
    def test_excellent(self):
        assert "Excellent" in rating_emoji(0.5)

    def test_good(self):
        assert "Good" in rating_emoji(1.5)

    def test_acceptable(self):
        assert "Acceptable" in rating_emoji(2.5)

    def test_needs_work(self):
        assert "Needs work" in rating_emoji(4.0)

    def test_boundary_excellent(self):
        assert "Excellent" in rating_emoji(1.0)

    def test_boundary_good(self):
        assert "Good" in rating_emoji(2.0)


# ---------------------------------------------------------------------------
# direction_hint
# ---------------------------------------------------------------------------

class TestDirectionHint:
    def test_on_target(self):
        assert "On target" in direction_hint(0.3127, 0.3127, "x")

    def test_decrease(self):
        result = direction_hint(0.32, 0.31, "Red Gain")
        assert "Decrease" in result
        assert "Red Gain" in result

    def test_increase(self):
        result = direction_hint(0.30, 0.31, "Green Gain")
        assert "Increase" in result
        assert "Green Gain" in result

    def test_within_deadband(self):
        """Difference < 0.001 should be on target."""
        assert "On target" in direction_hint(0.31270, 0.31275)


# ---------------------------------------------------------------------------
# Measurement.delta_uv
# ---------------------------------------------------------------------------

class TestDeltaUv:
    def test_d65_near_zero(self):
        """D65 (x=0.3127, y=0.3290) lies very close to the Planckian locus."""
        m = Measurement(x=0.3127, y=0.3290)
        assert abs(m.delta_uv) < 0.006

    def test_sign_positive_above_locus(self):
        """A point with higher v (greenish) should return positive Duv."""
        # Shift D65 upward in CIE 1960 UCS by increasing y
        m = Measurement(x=0.3127, y=0.3600)
        assert m.delta_uv > 0

    def test_sign_negative_below_locus(self):
        """A point with lower v (magenta-ish) should return negative Duv."""
        # Shift D65 downward in CIE 1960 UCS by decreasing y
        m = Measurement(x=0.3127, y=0.2900)
        assert m.delta_uv < 0

    def test_zero_chromaticity_returns_zero(self):
        """x=y=0 (black) should not produce an error."""
        m = Measurement(x=0.0, y=0.0)
        assert m.delta_uv == 0.0

    def test_magnitude_reasonable(self):
        """Duv should be small (< 0.05) for realistic display white points."""
        for x, y in [(0.3127, 0.3290), (0.3200, 0.3300), (0.2800, 0.2900)]:
            m = Measurement(x=x, y=y)
            assert abs(m.delta_uv) < 0.05
