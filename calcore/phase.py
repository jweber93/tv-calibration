from __future__ import annotations

from typing import Optional

from .models import Summary

# Grayscale quality thresholds for CMS phase unlock.
# Tightened per spec: avg < 0.5 dE, max < 1.0 dE.
_GRAY_AVG_THRESHOLD = 0.5
_GRAY_MAX_THRESHOLD = 1.0

# If CMS adjustments shift grayscale dE by more than this, flag a WB re-run.
_CMS_REGRESSION_THRESHOLD = 0.3


def determine_phase(summary: Summary, prev_phase: str) -> str:
    """Determine the next calibration phase based on current measurements.

    Uses tightened grayscale thresholds (avg < 0.5, max < 1.0) to gate CMS
    unlock per the sequential-dependency spec.
    """
    # Heuristic phase progression.
    if prev_phase == "baseline":
        return "wb"

    gray_ok = (
        summary.grayscale_avg_de is not None
        and summary.grayscale_avg_de < _GRAY_AVG_THRESHOLD
        and (summary.grayscale_max_de or 999) < _GRAY_MAX_THRESHOLD
    )
    gamma_ok = True
    if summary.gamma_midtones is not None:
        gamma_ok = abs(summary.gamma_midtones - 2.2) <= 0.1
    if summary.pq_err_midtones is not None:
        gamma_ok = abs(summary.pq_err_midtones) <= 5.0

    color_present = any(row["bucket"] in ("75", "100") for row in summary.color_rows)
    color_ok = False
    if color_present:
        c75 = summary.color_75_avg_de is not None and summary.color_75_avg_de <= 3.0
        c100 = (
            summary.color_100_avg_de is not None and summary.color_100_avg_de <= 3.0
        )
        color_ok = c75 or c100

    if prev_phase in ("wb", "mpwb"):
        if gray_ok and gamma_ok:
            return "cms" if color_present else "verify"
        return "mpwb"

    if prev_phase == "cms":
        if gray_ok and gamma_ok and color_ok:
            return "verify"
        return "cms"

    return prev_phase


def check_cms_regression(
    summary: Summary,
    baseline_de: float,
    threshold: float = _CMS_REGRESSION_THRESHOLD,
) -> Optional[str]:
    """Return a warning string if CMS adjustments have regressed grayscale dE.

    Call this after each CMS sweep, passing the grayscale_avg_de that was
    recorded when CMS phase began.  Returns None when the shift is acceptable.

    Args:
        summary: Current measurement summary.
        baseline_de: Grayscale avg dE at the moment CMS phase started.
        threshold: Maximum tolerated increase in avg dE (default 0.3).
    """
    if summary.grayscale_avg_de is None:
        return None
    delta = summary.grayscale_avg_de - baseline_de
    if delta > threshold:
        return (
            f"CMS adjustment shifted Grayscale dE by +{delta:.2f} "
            f"(baseline {baseline_de:.2f} → current {summary.grayscale_avg_de:.2f}) "
            f"— re-run White Balance"
        )
    return None
