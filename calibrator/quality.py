"""Quality-gate evaluation extracted from server.py."""

from __future__ import annotations

from typing import Any, Dict, Optional

from calcore.models import CalibrationTarget
from .profiles import TVProfile
from .guidance import gamma_nominal_pct
from .session import (
    grayscale_levels_for_ramp,
    latest_gamma_pass,
    latest_grayscale_pass,
    latest_wb_measurements,
    m_to_dict,
    gamma_levels_for_session,
)
from .utils import gamma_from_luminance

QG_WB_DE_THRESHOLD = 1.5
QG_CMS_DE_THRESHOLD = 3.0
QG_GAMMA_THRESHOLD = 0.05
QG_LUMINANCE_PCT = 5.0


def step_quality(session: Dict[str, Any], tv: Optional[TVProfile]) -> Dict[str, Any]:
    target: Optional[CalibrationTarget] = session.get("target")
    signal_range = session.get("signal_range", "auto")
    code_scale = session.get("code_scale", "8bit")
    gates: Dict[str, Any] = {}

    peak = session.get("peak_luminance", 0.0)
    if peak > 0 and target:
        target_nits = target.peak_luminance_nits
        pct_err = abs(peak - target_nits) / target_nits * 100
        gates["luminance"] = {
            "passed": pct_err <= QG_LUMINANCE_PCT,
            "score": round(pct_err, 1),
            "threshold": QG_LUMINANCE_PCT,
            "unit": "%",
            "label": f"Peak nits ±{QG_LUMINANCE_PCT:.0f}%",
            "detail": f"{peak:.0f} nits (target {target_nits:.0f})",
        }

    wb = session.get("wb_measurements", [])
    if wb and target:
        latest_wb = latest_wb_measurements(wb)
        gain_m = latest_wb.get("gain")
        offset_m = latest_wb.get("offset")
        gain_de = m_to_dict(gain_m, target, signal_range, code_scale)["delta_e"] if gain_m else None
        offset_de = m_to_dict(offset_m, target, signal_range, code_scale)["delta_e"] if offset_m else None
        scores = [x for x in (gain_de, offset_de) if x is not None]
        if scores:
            worst = max(scores)
            both_present = gain_de is not None and offset_de is not None
            parts = []
            if gain_de is not None:
                parts.append(f"Gain ΔE {gain_de:.2f}")
            if offset_de is not None:
                parts.append(f"Offset ΔE {offset_de:.2f}")
            gates["white_balance"] = {
                "passed": both_present and worst < QG_WB_DE_THRESHOLD,
                "score": round(worst, 2),
                "threshold": QG_WB_DE_THRESHOLD,
                "unit": "ΔE",
                "label": f"ΔE < {QG_WB_DE_THRESHOLD:.1f}",
                "detail": ", ".join(parts),
            }

    g_meas = session.get("gamma_measurements", [])
    if g_meas and target and target.gamma > 0:
        gamma_levels = gamma_levels_for_session(session)
        latest_pass = latest_gamma_pass(g_meas, gamma_levels, signal_range, code_scale)
        if latest_pass:
            peak_nits = target.peak_luminance_nits
            target_gamma = target.gamma
            deltas = []
            for measurement in latest_pass:
                stim_pct = gamma_nominal_pct(measurement, signal_range, code_scale, gamma_levels)
                if stim_pct is None:
                    continue
                gamma_value = gamma_from_luminance(measurement.Y, peak_nits, stim_pct)
                if gamma_value is not None:
                    deltas.append(abs(gamma_value - target_gamma))
            if deltas:
                worst_d = max(deltas)
                gates["gamma"] = {
                    "passed": worst_d <= QG_GAMMA_THRESHOLD,
                    "score": round(worst_d, 3),
                    "threshold": QG_GAMMA_THRESHOLD,
                    "unit": "Δγ",
                    "label": f"γ ±{QG_GAMMA_THRESHOLD}",
                    "detail": f"Worst Δγ {worst_d:.3f} (target γ {target_gamma})",
                }

    cms = session.get("cms_measurements", [])
    if cms and target and tv:
        expected_colours = set(tv.CMS_COLOURS)
        latest_per_colour = {}
        for measurement in cms:
            colour = (measurement.label or "").replace(" 100%", "")
            if colour in expected_colours:
                latest_per_colour[colour] = measurement
        if latest_per_colour:
            de_map = {c: m_to_dict(m, target, signal_range, code_scale)["delta_e"] for c, m in latest_per_colour.items()}
            worst_de = max(de_map.values())
            worst_colour = max(de_map, key=de_map.__getitem__)
            all_present = expected_colours == set(de_map)
            gates["color_tuner"] = {
                "passed": all_present and worst_de < QG_CMS_DE_THRESHOLD,
                "score": round(worst_de, 2),
                "threshold": QG_CMS_DE_THRESHOLD,
                "unit": "ΔE",
                "label": f"All ΔE < {QG_CMS_DE_THRESHOLD:.1f}",
                "detail": f"Worst: {worst_colour} ΔE {worst_de:.2f}" + ("" if all_present else f" ({len(de_map)}/{len(expected_colours)} colors)"),
            }

    for phase, key in (("pre_grayscale", "pre_measurements"), ("post_grayscale", "post_measurements")):
        measurements = session.get(key, [])
        if measurements:
            levels = grayscale_levels_for_ramp(session.get("grayscale_ramp_steps", 11))
            done = len(latest_grayscale_pass(measurements, max_count=len(levels), signal_range=signal_range, code_scale=code_scale))
            total = len(levels)
            gates[phase] = {
                "passed": done >= total,
                "score": done,
                "threshold": total,
                "unit": "patches",
                "label": f"{total}-point ramp",
                "detail": f"{done}/{total} patches",
            }

    return gates
