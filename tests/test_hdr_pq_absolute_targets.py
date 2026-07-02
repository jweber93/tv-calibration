"""Regression test for issue #543: PQ grayscale targets must be absolute.

ST.2084 (PQ) is an absolute EOTF — the encoded signal maps directly to nits,
not to a fraction of the display's peak luminance. A display that tracks the
PQ curve perfectly (clipping only above its own peak) must score ΔE ≈ 0 and
pq_err_midtones ≈ 0, not the peak-rescaled error the old code produced.
"""
from __future__ import annotations

import textwrap

from calcore.analysis import analyze
from calcore.csv_import import parse_measurement_csv
from calcore.eotf import pq_eotf, pq_target_nits
from calcore.models import AnalysisConfig

PEAK_NITS = 1000.0
CODE_MAX = 1023
WHITE_XY = (0.3127, 0.3290)

# A perfect 1000-nit PQ display: measured Y at each code value equals the
# absolute ST.2084 target, clipped at the panel's peak (its tone-map knee).
_PERFECT_PQ_RAMP_CSV = "\n".join(
    f"{i},{code},{code},{code},{min(pq_eotf(code / CODE_MAX), PEAK_NITS):.4f},"
    f"{WHITE_XY[0]},{WHITE_XY[1]}"
    for i, code in enumerate(
        (round(n / 10 * CODE_MAX) for n in range(1, 10)), start=1
    )
).encode() + b"\n"


def _analyze_ramp():
    cfg = AnalysisConfig(mode="hdr", eotf="pq", target_space="bt2020", code_max=CODE_MAX)
    patches = parse_measurement_csv(_PERFECT_PQ_RAMP_CSV, format="xyY")
    return analyze(patches, cfg, white_point_xy=WHITE_XY)


def test_perfect_pq_ramp_scores_near_zero_delta_e():
    summary = _analyze_ramp()
    assert summary.grayscale_avg_de is not None
    assert summary.grayscale_avg_de < 1.0, (
        f"grayscale_avg_de={summary.grayscale_avg_de:.3f} — PQ targets are still "
        "being rescaled by peak instead of treated as absolute nits"
    )


def test_perfect_pq_ramp_scores_near_zero_pq_err_midtones():
    summary = _analyze_ramp()
    assert summary.pq_err_midtones is not None
    assert abs(summary.pq_err_midtones) < 1.0, (
        f"pq_err_midtones={summary.pq_err_midtones:.3f}% — expected ~0 for a "
        "perfectly-tracking PQ display"
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
