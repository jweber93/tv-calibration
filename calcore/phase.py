from __future__ import annotations

from .models import Summary


def determine_phase(summary: Summary, prev_phase: str) -> str:
    # Heuristic phase progression.
    if prev_phase == "baseline":
        return "wb"

    gray_ok = (
        summary.grayscale_avg_de is not None
        and summary.grayscale_avg_de <= 2.0
        and (summary.grayscale_max_de or 999) <= 3.0
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
