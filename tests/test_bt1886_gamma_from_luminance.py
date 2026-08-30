"""Tests for calcore.eotf.bt1886_gamma_from_luminance (issue #637).

A pure through-origin power-law fit assumes Y(0) == 0. Real SDR panels have a
non-zero black floor, which BT.1886 absorbs via its ``lb`` term; fitting the
naive power law to such a panel biases the reported gamma low. This function
instead solves for the gamma that reproduces a measurement through
``bt1886_eotf`` at the panel's own measured black floor.
"""
from __future__ import annotations

import pytest

from calcore.eotf import bt1886_eotf, bt1886_gamma_from_luminance


class TestNoBlackFloor:
    """black_nits=0 (default) must reduce to the original power-law estimate."""

    def test_matches_plain_power_law(self):
        peak, gamma, stim = 100.0, 2.2, 0.5
        measured = peak * (stim ** gamma)
        assert bt1886_gamma_from_luminance(measured, peak, stim) == pytest.approx(
            gamma, abs=1e-6
        )

    def test_out_of_range_returns_none(self):
        assert bt1886_gamma_from_luminance(50, 120, 0.0) is None
        assert bt1886_gamma_from_luminance(50, 120, 1.0) is None
        assert bt1886_gamma_from_luminance(50, 0, 0.5) is None
        assert bt1886_gamma_from_luminance(0, 120, 0.5) is None
        assert bt1886_gamma_from_luminance(130, 120, 0.5) is None


class TestWithBlackFloor:
    """black_nits > 0 recovers the true gamma of a perfect BT.1886 panel."""

    @pytest.mark.parametrize("black_nits", [0.1, 0.5, 1.0, 2.0])
    @pytest.mark.parametrize("true_gamma", [2.2, 2.4])
    @pytest.mark.parametrize("stim", [0.2, 0.35, 0.5, 0.65, 0.8])
    def test_recovers_true_gamma(self, black_nits, true_gamma, stim):
        peak = 100.0
        measured = bt1886_eotf(stim, peak, black_nits, true_gamma)
        recovered = bt1886_gamma_from_luminance(measured, peak, stim, black_nits)
        assert recovered == pytest.approx(true_gamma, abs=1e-3)

    def test_naive_power_law_would_be_biased(self):
        """Sanity check that the scenario is non-trivial: the plain
        power-law estimate (black_nits=0) really is biased low here."""
        import math

        peak, black_nits, true_gamma, stim = 100.0, 0.5, 2.2, 0.5
        measured = bt1886_eotf(stim, peak, black_nits, true_gamma)
        naive = math.log(measured / peak) / math.log(stim)
        assert naive < true_gamma - 0.1

    def test_black_floor_reading_returns_the_floor(self):
        """At the panel's own 0% reading (measured == black), the signal
        fraction is 0 and out of the valid (0, 1) domain -> None."""
        assert bt1886_gamma_from_luminance(0.5, 100.0, 0.0, 0.5) is None

    def test_measured_at_or_below_floor_returns_none(self):
        assert bt1886_gamma_from_luminance(0.4, 100.0, 0.5, 0.5) is None
        assert bt1886_gamma_from_luminance(0.5, 100.0, 0.5, 0.5) is None

    def test_measured_above_peak_returns_none(self):
        assert bt1886_gamma_from_luminance(101.0, 100.0, 0.5, 0.5) is None

    def test_zero_peak_returns_none(self):
        assert bt1886_gamma_from_luminance(10.0, 0.0, 0.5, 0.5) is None
