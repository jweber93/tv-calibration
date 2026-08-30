"""Regression tests for issue #637.

The midtone gamma estimator used to be a pure through-origin power law
(``log(Y/peak) / log(n)``), which implicitly assumes the panel's luminance is
zero at 0% signal. Real SDR panels have a non-zero measured black floor —
BT.1886 absorbs that via its ``lb`` term — so fitting the naive power law to
such a panel reported a biased-low gamma even when it tracked BT.1886
perfectly, and the phase/quality gates (``abs(gamma_midtones - 2.2) <= 0.1``,
``QG_GAMMA_THRESHOLD``) failed essentially every real SDR panel.

These tests build a synthetic panel that tracks BT.1886 exactly (true gamma
2.2, D65 white point throughout so dE ~= 0) at a range of realistic black
floors, and assert the estimator recovers the true gamma regardless.
"""
from __future__ import annotations

import pytest

from calcore.analysis import analyze
from calcore.csv_import import parse_measurement_csv
from calcore.eotf import bt1886_eotf
from calcore.models import AnalysisConfig
from calcore.phase import determine_phase

CFG = AnalysisConfig(mode="sdr", eotf="bt1886", target_space="bt709", code_max=100)
PEAK_NITS = 100.0
TRUE_GAMMA = 2.2


def _perfect_panel_csv(black_floor: float) -> bytes:
    """11-point grayscale ramp for a perfect BT.1886 SDR panel.

    Luminance follows ``bt1886_eotf`` exactly at the given black floor, and
    the chromaticity is fixed at D65 for every patch, so the only thing under
    test is whether the gamma estimator recovers the true gamma of 2.2.
    """
    lines = []
    for i, code in enumerate(range(0, 101, 10)):
        n = code / 100.0
        y = bt1886_eotf(n, PEAK_NITS, black_floor, TRUE_GAMMA)
        # Purely numeric label — a header-detection heuristic in
        # parse_measurement_csv treats any letters in the first line as a
        # header row (see calcore/csv_import.py), so keep this numeric like
        # the golden fixtures do.
        lines.append(f"{i},{code},{code},{code},{y:.6f},0.3127,0.3290")
    return ("\n".join(lines) + "\n").encode()


def _analyze(black_floor: float):
    patches = parse_measurement_csv(_perfect_panel_csv(black_floor), format="xyY")
    return analyze(patches, CFG)


class TestGammaMidtonesWithBlackFloor:
    @pytest.mark.parametrize("black_floor", [0.0, 0.5, 1.0, 2.0])
    def test_recovers_true_gamma_regardless_of_floor(self, black_floor):
        summary = _analyze(black_floor)
        assert summary.gamma_midtones == pytest.approx(TRUE_GAMMA, abs=0.01), (
            f"black_floor={black_floor}: gamma_midtones={summary.gamma_midtones} "
            "should track the panel's true gamma"
        )

    @pytest.mark.parametrize("black_floor", [0.5, 1.0, 2.0])
    def test_zero_black_floor_and_nonzero_agree_closely(self, black_floor):
        """The fix shouldn't make the estimate *depend* on the floor — a
        perfect panel reports ~2.2 whether or not it has a black floor."""
        no_floor = _analyze(0.0).gamma_midtones
        with_floor = _analyze(black_floor).gamma_midtones
        assert with_floor == pytest.approx(no_floor, abs=0.02)


class TestPhaseGateWithBlackFloor:
    """The progression gate in calcore/phase.py must not stall a perfect
    panel in 'mpwb' just because it has a realistic non-zero black level."""

    @pytest.mark.parametrize("black_floor", [0.5, 1.0, 2.0])
    def test_progresses_past_mpwb_for_realistic_black_floor(self, black_floor):
        summary = _analyze(black_floor)
        # No color patches in this fixture, so a passing gamma+grayscale gate
        # should advance straight to 'verify'.
        assert determine_phase(summary, "mpwb") == "verify", (
            f"black_floor={black_floor}: gamma_midtones="
            f"{summary.gamma_midtones} should pass the +/-0.1 gamma gate"
        )
