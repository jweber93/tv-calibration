"""Tests for colour-science helpers exposed by the calibrator package."""
import ast
from pathlib import Path

import pytest

from calcore.eotf import pq_eotf, pq_inverse_eotf
from calibrator import (
    xyY_to_XYZ,
    XYZ_to_lab,
    xyY_to_lab,
    delta_e_cie76,
    delta_xy,
    eotf_from_luminance,
    gamma_from_luminance,
    is_pq_eotf,
    rating_emoji,
    direction_hint,
    D65_XY,
    D65_XYZ,
    Measurement,
)


def test_runtime_module_does_not_call_ensure_packages_at_import_time():
    source = Path("calibrator/runtime.py").read_text()
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
        """y=0 should project onto D65 if Y > 0."""
        X, Y, Z = xyY_to_XYZ(0.3, 0.0, 50.0)
        assert Y == 50.0
        assert X == pytest.approx(47.5228, rel=1e-3)
        assert Z == pytest.approx(54.4529, rel=1e-3)

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


class TestGammaFromLuminanceBlackFloor:
    """black_nits (issue #637): a measured black floor makes the estimate
    BT.1886-aware instead of a naive through-origin power law."""

    def test_recovers_true_gamma_with_black_floor(self):
        from calcore.eotf import bt1886_eotf

        peak, black_nits, stim = 100.0, 0.5, 50
        measured = bt1886_eotf(stim / 100.0, peak, black_nits, 2.2)
        g = gamma_from_luminance(measured, peak, stim, black_nits)
        assert g == pytest.approx(2.2, abs=1e-3)

    def test_default_black_nits_matches_legacy_power_law(self):
        peak, stim = 120.0, 50
        measured = peak * (stim / 100.0) ** 2.2
        assert gamma_from_luminance(measured, peak, stim) == pytest.approx(
            gamma_from_luminance(measured, peak, stim, 0.0)
        )


# ---------------------------------------------------------------------------
# pq_inverse_eotf / is_pq_eotf / eotf_from_luminance
# ---------------------------------------------------------------------------

class TestPqInverseEotf:
    @pytest.mark.parametrize("n", [0.1, 0.25, 0.5, 0.75, 0.9])
    def test_round_trips_with_pq_eotf(self, n):
        """pq_inverse_eotf is the exact inverse of pq_eotf."""
        assert pq_inverse_eotf(pq_eotf(n)) == pytest.approx(n, abs=1e-9)

    def test_zero_nits_maps_to_near_zero_signal(self):
        # PQ encoding of 0 nits is a tiny positive offset, not exactly 0.
        assert pq_inverse_eotf(0.0) == pytest.approx(0.0, abs=1e-5)

    def test_peak_10000_maps_to_unity(self):
        assert pq_inverse_eotf(10000.0) == pytest.approx(1.0, abs=1e-9)


class TestIsPqEotf:
    @pytest.mark.parametrize("label", ["pq", "PQ", "PQ (ST.2084)", "st2084", "x2084"])
    def test_detects_pq(self, label):
        assert is_pq_eotf(label) is True

    @pytest.mark.parametrize("label", ["gamma", "BT.1886", "2.2", "", None])
    def test_rejects_non_pq(self, label):
        assert is_pq_eotf(label) is False


class TestEotfFromLuminance:
    def test_power_law_matches_gamma_from_luminance(self):
        """Non-PQ EOTFs keep the legacy power-law effective gamma."""
        peak = 100.0
        measured = peak * (0.5) ** 2.2
        assert eotf_from_luminance(measured, peak, 50, "BT.1886") == pytest.approx(
            gamma_from_luminance(measured, peak, 50)
        )

    def test_default_eotf_is_power_law(self):
        peak = 120.0
        measured = peak * (0.4) ** 2.4
        assert eotf_from_luminance(measured, peak, 40) == pytest.approx(2.4, abs=0.01)

    @pytest.mark.parametrize("stim", [20, 40, 60, 80])
    def test_perfect_pq_tracking_is_one(self, stim):
        """A display that exactly follows PQ reports a tracking value of 1.0."""
        measured = pq_eotf(stim / 100.0)
        assert eotf_from_luminance(measured, 1000.0, stim, "PQ (ST.2084)") == pytest.approx(
            1.0, abs=1e-6
        )

    def test_pq_too_dark_above_one(self):
        measured = pq_eotf(0.5) * 0.5  # half the reference luminance
        assert eotf_from_luminance(measured, 1000.0, 50, "pq") > 1.0

    def test_pq_too_bright_below_one(self):
        measured = pq_eotf(0.5) * 2.0  # double the reference luminance
        assert eotf_from_luminance(measured, 1000.0, 50, "pq") < 1.0

    def test_pq_does_not_use_plain_gamma(self):
        """The PQ path must diverge from the plain power-law value."""
        measured = pq_eotf(0.5)
        pq_val = eotf_from_luminance(measured, 1000.0, 50, "pq")
        plain = eotf_from_luminance(measured, 1000.0, 50, "gamma")
        assert pq_val == pytest.approx(1.0, abs=1e-6)
        assert plain != pytest.approx(1.0, abs=0.1)

    def test_pq_zero_nits_returns_none(self):
        assert eotf_from_luminance(0.0, 1000.0, 50, "pq") is None

    def test_boundary_stimulus_returns_none(self):
        assert eotf_from_luminance(50, 1000.0, 0, "pq") is None
        assert eotf_from_luminance(50, 1000.0, 100, "pq") is None


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


# ---------------------------------------------------------------------------
# Measurement.cct (Correlated Color Temperature)
# ---------------------------------------------------------------------------

class TestCCT:
    def test_normal_cct_calculation(self):
        """D65 should give a CCT around 6500K."""
        m = Measurement(x=0.3127, y=0.3290, Y=100.0)
        assert m.cct is not None
        assert 6400 < m.cct < 6700

    def test_zero_luminance_returns_none(self):
        m = Measurement(x=0.3127, y=0.3290, Y=0.0)
        assert m.cct is None

    def test_zero_chromaticity_returns_none(self):
        m = Measurement(x=0.0, y=0.0, Y=100.0)
        assert m.cct is None

    def test_y_near_boundary_returns_none(self):
        """y values extremely close to 0.1858 should return None to avoid division issues."""
        m = Measurement(x=0.32, y=0.1858, Y=100.0)
        assert m.cct is None

    def test_y_slightly_below_boundary(self):
        """y = 0.1857 should be safe and return a valid CCT."""
        m = Measurement(x=0.32, y=0.1857, Y=100.0)
        assert m.cct is not None

    def test_y_slightly_above_boundary(self):
        """y = 0.1859 should be safe and return a valid CCT."""
        m = Measurement(x=0.32, y=0.1859, Y=100.0)
        assert m.cct is not None

    def test_ieee754_precision_edge_case(self):
        """Test values that could arise from IEEE 754 floating-point arithmetic.

        A value computed as 0.1858 in code might actually be:
        - 0.18579999999999997 (slightly below)
        - 0.18580000000000002 (slightly above)

        Both should be handled gracefully by math.isclose().
        """
        # Simulate floating-point representation error
        y_below = 0.18579999999999997
        y_above = 0.18580000000000002

        m_below = Measurement(x=0.32, y=y_below, Y=100.0)
        m_above = Measurement(x=0.32, y=y_above, Y=100.0)

        # Both should return None since they're within tolerance of 0.1858
        assert m_below.cct is None
        assert m_above.cct is None

    def test_cct_stability_across_range(self):
        """CCT should produce stable values for typical white points."""
        test_cases = [
            (0.3127, 0.3290),  # D65
            (0.345, 0.359),    # warmer
            (0.295, 0.315),    # cooler
        ]
        for x, y in test_cases:
            m = Measurement(x=x, y=y, Y=100.0)
            assert m.cct is not None
            assert 1000 < m.cct < 25000  # Reasonable CCT range
