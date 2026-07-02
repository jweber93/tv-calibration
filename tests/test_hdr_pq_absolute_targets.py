"""Regression test for issue #543: PQ grayscale targets must be absolute.

ST.2084 (PQ) is an absolute EOTF — the encoded signal maps directly to nits,
not to a fraction of the display's peak luminance. A display that tracks the
PQ curve perfectly (clipping only above its own peak) must score ΔE ≈ 0 and
pq_err_midtones ≈ 0, not the peak-rescaled error the old code produced.
"""
from __future__ import annotations

import pytest

from calcore.analysis import analyze
from calcore.csv_import import parse_measurement_csv
from calcore.eotf import pq_eotf, pq_target_nits
from calcore.models import AnalysisConfig

CODE_MAX = 1023
WHITE_XY = (0.3127, 0.3290)


def _perfect_pq_ramp_csv(peak_nits: float) -> bytes:
    # A perfect PQ display at the given peak: measured Y at each code value
    # equals the absolute ST.2084 target, clipped at the panel's peak (its
    # tone-map knee).
    rows = (
        f"{i},{code},{code},{code},{min(pq_eotf(code / CODE_MAX), peak_nits):.4f},"
        f"{WHITE_XY[0]},{WHITE_XY[1]}"
        for i, code in enumerate(
            (round(n / 10 * CODE_MAX) for n in range(1, 10)), start=1
        )
    )
    return ("\n".join(rows) + "\n").encode()


def _analyze_ramp(peak_nits: float):
    cfg = AnalysisConfig(mode="hdr", eotf="pq", target_space="bt2020", code_max=CODE_MAX)
    patches = parse_measurement_csv(_perfect_pq_ramp_csv(peak_nits), format="xyY")
    return analyze(patches, cfg, white_point_xy=WHITE_XY)


@pytest.mark.parametrize("peak_nits", [1000.0, 500.0])
def test_perfect_pq_ramp_scores_near_zero_delta_e(peak_nits):
    summary = _analyze_ramp(peak_nits)
    assert summary.grayscale_avg_de is not None
    assert summary.grayscale_avg_de < 1.0, (
        f"[peak={peak_nits}] grayscale_avg_de={summary.grayscale_avg_de:.3f} — PQ "
        "targets are still being rescaled by peak instead of treated as absolute nits"
    )


@pytest.mark.parametrize("peak_nits", [1000.0, 500.0])
def test_perfect_pq_ramp_scores_near_zero_pq_err_midtones(peak_nits):
    summary = _analyze_ramp(peak_nits)
    assert summary.pq_err_midtones is not None
    assert abs(summary.pq_err_midtones) < 1.0, (
        f"[peak={peak_nits}] pq_err_midtones={summary.pq_err_midtones:.3f}% — "
        "expected ~0 for a perfectly-tracking PQ display"
    )


def test_pq_target_nits_is_absolute_below_knee():
    # Below the panel's peak, the target is the raw absolute ST.2084 value —
    # not scaled by peak_nits (the historical bug divided by 10000 then
    # multiplied by peak, which only equals pq_eotf(n) when peak == 10000).
    assert pq_target_nits(0.5, 1000.0) == pq_eotf(0.5)
    assert pq_eotf(0.5) < 100.0  # ~92.25 nits, not ~9.2 nits


def test_pq_target_nits_clips_at_panel_peak():
    # Above the knee, the display physically cannot exceed its own peak.
    assert pq_target_nits(1.0, 1000.0) == 1000.0


def test_pq_target_nits_clips_at_a_lower_peak():
    # A 500-nit display (e.g. an entry-level HDR10 panel) clips well before
    # the 1000-nit reference: pq_eotf(0.7) is ~621 nits, already above 500.
    assert pq_eotf(0.7) > 500.0
    assert pq_target_nits(0.7, 500.0) == 500.0
    # But a lower stimulus that's still below the 500-nit knee stays absolute.
    assert pq_eotf(0.4) < 500.0
    assert pq_target_nits(0.4, 500.0) == pq_eotf(0.4)
