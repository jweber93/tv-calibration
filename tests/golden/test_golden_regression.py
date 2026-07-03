"""
Golden dataset regression tests.

These tests protect against drift in calcore.analysis.analyze() output.
They compare computed metrics against stored baselines; a failing test means
the algorithm or data has changed — review the diff before updating.

Run normally:        pytest tests/golden/
Update baselines:    pytest tests/golden/ --update-golden
"""
from __future__ import annotations

import textwrap
from typing import Any, Dict

import pytest

from calcore.analysis import analyze
from calcore.csv_import import parse_measurement_csv
from calcore.models import AnalysisConfig

from tests.golden.conftest import assert_within_golden_tolerance

# ── Reference CSV payloads ────────────────────────────────────────────────────
# Comma-separated headerless format: label, R, G, B, Y_lum, x_chrom, y_chrom
# Columns 5-7 (x+y < 1.0) are auto-detected as xyY by parse_measurement_csv.
#
# SDR BT.709: 11-point grayscale at 100 nit peak, gamma ≈ 2.35 (slightly high).
# Slightly warm WP (x=0.314) → grayscale_avg_de ≈ 1–3, gamma_midtones ≈ 2.35.

_SDR_REFERENCE_CSV = textwrap.dedent("""\
    1,16,16,16,0.010,0.3140,0.3300
    2,26,26,26,0.460,0.3140,0.3300
    3,51,51,51,2.350,0.3145,0.3305
    4,77,77,77,6.700,0.3148,0.3308
    5,102,102,102,13.80,0.3142,0.3298
    6,128,128,128,23.50,0.3140,0.3295
    7,153,153,153,36.90,0.3138,0.3292
    8,179,179,179,54.20,0.3136,0.3290
    9,204,204,204,73.40,0.3130,0.3285
    10,230,230,230,89.60,0.3128,0.3282
    11,235,235,235,99.80,0.3127,0.3279
""").encode()

# HDR10/PQ: 11-point grayscale at ~995 nit measured peak. Fixture Y values are
# representative measured luminances (not a perfect ST.2084 tracker), so the
# resulting ΔE/pq_err baselines below reflect real-world tracking error.
_HDR_REFERENCE_CSV = textwrap.dedent("""\
    1,64,64,64,0.010,0.3127,0.3290
    2,102,102,102,0.455,0.3127,0.3290
    3,204,204,204,3.850,0.3127,0.3290
    4,307,307,307,16.30,0.3127,0.3290
    5,409,409,409,52.40,0.3127,0.3290
    6,511,511,511,113.0,0.3127,0.3290
    7,614,614,614,218.0,0.3127,0.3290
    8,716,716,716,376.0,0.3127,0.3290
    9,818,818,818,597.0,0.3127,0.3290
    10,921,921,921,815.0,0.3127,0.3290
    11,1023,1023,1023,995.0,0.3127,0.3290
""").encode()


def _summary_to_dict(summary) -> Dict[str, Any]:
    """Extract numeric scalar metrics from a Summary for baseline comparison."""
    return {
        "grayscale_avg_de": summary.grayscale_avg_de,
        "grayscale_max_de": summary.grayscale_max_de,
        "grayscale_over_3": summary.grayscale_over_3,
        "gamma_midtones": summary.gamma_midtones,
        "pq_err_midtones": summary.pq_err_midtones,
        "color_75_avg_de": summary.color_75_avg_de,
        "color_75_max_de": summary.color_75_max_de,
        "color_100_avg_de": summary.color_100_avg_de,
        "color_100_max_de": summary.color_100_max_de,
        "measured_peak_y": summary.meta.get("measured_peak_y"),
    }


# ── SDR grayscale golden test ─────────────────────────────────────────────────

class TestSDRGrayscaleGolden:
    BASELINE_NAME = "sdr_bt709_grayscale"
    CFG = AnalysisConfig(mode="sdr", eotf="bt1886", target_space="bt709", code_max=235)

    def _run(self):
        patches = parse_measurement_csv(_SDR_REFERENCE_CSV, format="xyY")
        return analyze(patches, self.CFG)

    def test_grayscale_avg_de_stable(self, golden_baseline, save_golden, update_golden):
        summary = self._run()
        actual = _summary_to_dict(summary)

        if update_golden:
            save_golden(self.BASELINE_NAME, actual)
            return

        baseline = golden_baseline(self.BASELINE_NAME)
        if baseline is None:
            pytest.skip(
                f"No baseline for '{self.BASELINE_NAME}'. "
                "Run with --update-golden to create it."
            )

        assert_within_golden_tolerance(actual, baseline)

    def test_grayscale_patch_count_unchanged(self):
        summary = self._run()
        assert summary.measured_patch_count == 11, (
            "Reference CSV has 11 grayscale patches; count changed unexpectedly"
        )

    def test_no_grayscale_patches_over_threshold(self):
        summary = self._run()
        assert summary.grayscale_avg_de is not None
        assert summary.grayscale_avg_de < 5.0, (
            "SDR reference data has avg ΔE > 5 — parse or algorithm regression"
        )

    def test_gamma_midtones_plausible(self):
        summary = self._run()
        if summary.gamma_midtones is not None:
            assert 1.5 < summary.gamma_midtones < 3.5, (
                f"gamma_midtones={summary.gamma_midtones:.3f} is outside 1.5–3.5 "
                "(expected ~2.2 for SDR BT.1886)"
            )

    def test_peak_luminance_reasonable(self):
        summary = self._run()
        peak = summary.meta.get("measured_peak_y")
        assert peak is not None
        assert 50.0 < peak < 500.0, f"SDR peak={peak} nit is implausible"


# ── HDR10/PQ grayscale golden test ───────────────────────────────────────────

class TestHDRGrayscaleGolden:
    BASELINE_NAME = "hdr_pq_p3d65_grayscale"
    CFG = AnalysisConfig(mode="hdr", eotf="pq", target_space="p3d65", code_max=1023)

    def _run(self):
        patches = parse_measurement_csv(_HDR_REFERENCE_CSV, format="xyY")
        return analyze(patches, self.CFG)

    def test_hdr_golden_regression(self, golden_baseline, save_golden, update_golden):
        summary = self._run()
        actual = _summary_to_dict(summary)

        if update_golden:
            save_golden(self.BASELINE_NAME, actual)
            return

        baseline = golden_baseline(self.BASELINE_NAME)
        if baseline is None:
            pytest.skip(
                f"No baseline for '{self.BASELINE_NAME}'. "
                "Run with --update-golden to create it."
            )

        assert_within_golden_tolerance(actual, baseline)

    def test_pq_error_midtones_plausible(self):
        summary = self._run()
        if summary.pq_err_midtones is not None:
            assert abs(summary.pq_err_midtones) < 20.0, (
                f"pq_err_midtones={summary.pq_err_midtones:.2f}% exceeds ±20% — "
                "likely an EOTF regression"
            )

    def test_peak_luminance_reasonable(self):
        summary = self._run()
        peak = summary.meta.get("measured_peak_y")
        assert peak is not None
        assert 400.0 < peak < 2000.0, f"HDR peak={peak} nit is implausible"


# ── Cross-model regression: verify analyze() is deterministic ─────────────────

@pytest.mark.parametrize("csv_bytes,cfg,label", [
    (_SDR_REFERENCE_CSV, AnalysisConfig(mode="sdr", eotf="bt1886", target_space="bt709", code_max=235), "sdr"),
    (_HDR_REFERENCE_CSV, AnalysisConfig(mode="hdr", eotf="pq", target_space="p3d65", code_max=1023), "hdr"),
])
def test_analyze_is_deterministic(csv_bytes, cfg, label):
    """analyze() must return identical results on repeated calls with identical input."""
    patches = parse_measurement_csv(csv_bytes, format="xyY")
    r1 = analyze(patches, cfg)
    r2 = analyze(patches, cfg)
    assert r1.grayscale_avg_de == pytest.approx(r2.grayscale_avg_de, abs=1e-9), (
        f"[{label}] Non-deterministic grayscale_avg_de"
    )
    # gamma_midtones is None for PQ/HDR (pq_err_midtones is used instead).
    assert r1.gamma_midtones == r2.gamma_midtones or (
        r1.gamma_midtones is not None
        and r2.gamma_midtones is not None
        and r1.gamma_midtones == pytest.approx(r2.gamma_midtones, abs=1e-9)
    ), f"[{label}] Non-deterministic gamma_midtones"
