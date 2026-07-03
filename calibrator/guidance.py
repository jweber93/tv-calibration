"""Guidance, control-plan, and colour-target helpers extracted from server.py."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from calcore.models import (
    CalibrationTarget,
    Measurement,
)
from .profiles import TVProfile
from .utils import (
    delta_xy,
    eotf_from_luminance,
    is_pq_eotf,
    pq_point_above_knee,
    stimulus_pct_from_code_value,
)

GAMMA_TRACKING_LEVELS = (20, 40, 60, 80)
FINE_GAMMA_TRACKING_LEVELS = tuple(range(5, 100, 5))
GAMMA_WORKFLOW_LEVELS = {
    "quick": GAMMA_TRACKING_LEVELS,
    "fine": FINE_GAMMA_TRACKING_LEVELS,
}
GAMMA_WORKFLOW_LABELS = {
    "quick": "Quick (20/40/60/80)",
    "fine": "Fine (5% increments)",
}
STIMULUS_TOLERANCE_PCT = 7.0


def measurement_stimulus_pct(
    m: Measurement,
    signal_range: str = "auto",
    code_scale: str = "8bit",
) -> Optional[float]:
    if not m.stimulus_rgb:
        return None
    decode_range = "full10" if signal_range == "full" and code_scale == "10bit" else signal_range
    return stimulus_pct_from_code_value(m.stimulus_rgb[0], decode_range)


def gamma_target_for_measurement(
    m: Measurement,
    levels: Tuple[int, ...] = GAMMA_TRACKING_LEVELS,
    signal_range: str = "auto",
    code_scale: str = "8bit",
) -> Optional[int]:
    stim_pct = measurement_stimulus_pct(m, signal_range, code_scale)
    if stim_pct is None:
        return None
    matches = [target_pct for target_pct in levels if abs(stim_pct - target_pct) <= STIMULUS_TOLERANCE_PCT]
    if not matches:
        return None
    return min(matches, key=lambda target_pct: abs(stim_pct - target_pct))


def gamma_nominal_pct(
    m: Measurement,
    signal_range: str = "auto",
    code_scale: str = "8bit",
    levels: Optional[Tuple[int, ...]] = None,
) -> Optional[float]:
    if levels is not None:
        target_pct = gamma_target_for_measurement(m, levels, signal_range, code_scale)
        if target_pct is not None:
            return float(target_pct)
    label = m.label or ""
    if label.startswith("Gamma "):
        try:
            return float(label.split(" ", 1)[1].rstrip("%"))
        except ValueError:
            pass
    target_pct = gamma_target_for_measurement(
        m,
        FINE_GAMMA_TRACKING_LEVELS,
        signal_range,
        code_scale,
    )
    if target_pct is not None:
        return float(target_pct)
    return measurement_stimulus_pct(m, signal_range, code_scale)


def wb_hints(m: Measurement, target_xy: Tuple[float, float]) -> Dict[str, Any]:
    dx = m.x - target_xy[0]
    dy = m.y - target_xy[1]
    shift = delta_xy(m.xy, target_xy)
    x_status = "high" if dx > 0.003 else ("low" if dx < -0.003 else "ok")
    y_status = "high" if dy > 0.003 else ("low" if dy < -0.003 else "ok")
    return {
        "x": {
            "status": x_status,
            "value": round(dx, 4),
            "action": {
                "high": "Decrease Red Gain or Increase Blue Gain",
                "low": "Increase Red Gain or Decrease Blue/Green Gain",
                "ok": "On target",
            }[x_status],
        },
        "y": {
            "status": y_status,
            "value": round(dy, 4),
            "action": {
                "high": "Decrease Green Gain",
                "low": "Increase Green Gain (or decrease Red+Blue)",
                "ok": "On target",
            }[y_status],
        },
        "total_shift": round(shift, 4),
        "overall": (
            "excellent" if shift < 0.003 else
            "close" if shift < 0.005 else
            "needs_work"
        ),
    }


def wb_amount_for(delta: float, boost: float = 0.0) -> int:
    return 2 if abs(delta) + boost >= 0.006 else 1


def wb_control_name(tv: TVProfile, color: str, type_: str) -> str:
    for key in tv.WB_2POINT:
        kl = key.lower()
        if color in kl and type_ in kl:
            return key
    short = color[0].upper()
    return f"{short}-{type_.capitalize()}"


def wb_measurement_type(m: Measurement) -> str:
    if "80%" in m.label or "Gain" in m.label:
        return "gain"
    if "30%" in m.label or "Offset" in m.label:
        return "offset"
    stim_pct = measurement_stimulus_pct(m)
    if stim_pct is not None:
        if abs(stim_pct - 80) <= 2:
            return "gain"
        if abs(stim_pct - 30) <= 2:
            return "offset"
    return "other"


def wb_control_candidates(m: Measurement, hints: Dict[str, Any], tv: TVProfile) -> List[Dict[str, Any]]:
    measurement_type = wb_measurement_type(m)
    if measurement_type not in ("gain", "offset"):
        return []

    type_key = "gain" if measurement_type == "gain" else "offset"
    r_ctrl = wb_control_name(tv, "red", type_key)
    g_ctrl = wb_control_name(tv, "green", type_key)
    b_ctrl = wb_control_name(tv, "blue", type_key)
    dx = float(hints["x"]["value"])
    dy = float(hints["y"]["value"])

    candidates: List[Dict[str, Any]] = []

    def add(control, direction, amount, reason, score, label, kind):
        candidates.append(
            {
                "control": control,
                "direction": direction,
                "amount": amount,
                "reason": reason,
                "score": round(score, 4),
                "label": label,
                "kind": kind,
            }
        )

    if hints["x"]["status"] == "high":
        add(r_ctrl, "down", wb_amount_for(dx), "CIE x too high — red needs to come down.", abs(dx) + 0.02, "primary", "x_primary")
        if hints["y"]["status"] != "high":
            add(b_ctrl, "up", wb_amount_for(dx), "Blue backup: pull x left without lowering green.", abs(dx), "secondary", "x_alt_blue")
    elif hints["x"]["status"] == "low":
        add(r_ctrl, "up", wb_amount_for(dx), "CIE x too low — red needs to come up.", abs(dx) + 0.02, "primary", "x_primary")
        if hints["y"]["status"] != "low":
            add(b_ctrl, "down", wb_amount_for(dx), "Blue backup: push x right without adding green.", abs(dx), "secondary", "x_alt_blue")

    if hints["y"]["status"] == "high":
        add(g_ctrl, "down", wb_amount_for(dy), "CIE y too high — green needs to come down.", abs(dy) + 0.02, "primary", "y_primary")
        if hints["x"]["status"] != "high":
            add(b_ctrl, "up", wb_amount_for(dy), "Blue backup: adding blue pulls white point down.", abs(dy), "secondary", "y_alt_blue")
    elif hints["y"]["status"] == "low":
        add(g_ctrl, "up", wb_amount_for(dy), "CIE y too low — green needs to come up.", abs(dy) + 0.02, "primary", "y_primary")
        if hints["x"]["status"] != "low":
            add(b_ctrl, "down", wb_amount_for(dy), "Blue backup: lowering blue pushes white point up.", abs(dy), "secondary", "y_alt_blue")

    if hints["x"]["status"] == "high" and hints["y"]["status"] == "high":
        add(
            b_ctrl,
            "up",
            wb_amount_for(dx, abs(dy)),
            "Both x and y high — increasing blue moves diagonally toward D65.",
            abs(dx) + abs(dy) + 0.05,
            "primary",
            "blue_combo",
        )
    elif hints["x"]["status"] == "low" and hints["y"]["status"] == "low":
        add(
            b_ctrl,
            "down",
            wb_amount_for(dx, abs(dy)),
            "Both x and y low — lowering blue moves diagonally toward D65.",
            abs(dx) + abs(dy) + 0.05,
            "primary",
            "blue_combo",
        )

    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in candidates:
        key = (item["control"], item["direction"])
        existing = merged.get(key)
        if existing is None or item["score"] > existing["score"]:
            merged[key] = item
        elif item["reason"] not in existing["reason"]:
            existing["reason"] = f'{existing["reason"]} {item["reason"]}'

    return sorted(merged.values(), key=lambda i: (i["label"] != "primary", -i["score"], i["control"]))


def wb_control_plan(
    m: Measurement,
    hints: Dict[str, Any],
    tv: TVProfile,
    de: Optional[float] = None,
) -> List[Dict[str, Any]]:
    ranked = wb_control_candidates(m, hints, tv)
    controls: List[Dict[str, Any]] = []
    for idx, item in enumerate(ranked[:2], start=1):
        controls.append(
            {
                "control": item["control"],
                "direction": item["direction"],
                "amount": item["amount"],
                "priority": "primary" if idx == 1 else "secondary",
                "summary": f'{"Primary" if idx == 1 else "Secondary"}: {"Raise" if item["direction"] == "up" else "Lower"} {item["control"]} by {item["amount"]}',
                "reason": item["reason"],
            }
        )
    if not controls:
        measurement_type = wb_measurement_type(m)
        suffix_label = "Gain" if measurement_type == "gain" else "Offset"
        # Chromaticity is on target but ΔE may still be high due to luminance error.
        if de is not None and de > 3.0:
            reason = (
                f"Chromaticity on target — no RGB {suffix_label.lower()} adjustment needed. "
                f"ΔE {de:.1f} is elevated due to luminance error at this stimulus level, "
                f"not a color cast. This will be addressed during gamma calibration; "
                f"do not adjust RGB {suffix_label.lower()} controls for it."
            )
        else:
            reason = "This reading is already within tolerance."
        controls.append(
            {
                "control": f"{suffix_label} controls",
                "direction": "hold",
                "amount": 0,
                "priority": "primary",
                "summary": f"Leave {suffix_label} as-is",
                "reason": reason,
            }
        )
    return controls[:3]


def wb_recommendations(
    m: Measurement,
    hints: Dict[str, Any],
    tv: Optional[TVProfile] = None,
    de: Optional[float] = None,
) -> List[str]:
    recs: List[str] = []
    is_gain = "80%" in m.label or "Gain" in m.label
    is_offset = "30%" in m.label or "Offset" in m.label
    if is_gain:
        recs.append("Stay in the Gain controls first. Get 80% gray close before touching Offset.")
    elif is_offset:
        recs.append("Use Offset only for the 30% pass. Re-check 80% gray after shadow changes.")

    if tv is not None:
        plan = wb_control_candidates(m, hints, tv)[:2]
        if plan:
            recs.append(f'Primary move: {"Raise" if plan[0]["direction"] == "up" else "Lower"} {plan[0]["control"]} by {plan[0]["amount"]}')
            if len(plan) > 1:
                recs.append(f'Secondary option: {"Raise" if plan[1]["direction"] == "up" else "Lower"} {plan[1]["control"]} by {plan[1]["amount"]}')
    else:
        if hints["x"]["status"] != "ok":
            recs.append(hints["x"]["action"])
        if hints["y"]["status"] != "ok":
            recs.append(hints["y"]["action"])

    if hints["overall"] == "excellent":
        if de is not None and de > 3.0:
            recs.append(
                f"Chromaticity on target. ΔE {de:.1f} is elevated due to luminance error — "
                "not a color cast. Address during gamma calibration."
            )
        else:
            recs.append("White balance is within tolerance. Re-measure the other gray point and move on.")
    elif hints["overall"] == "close":
        recs.append("Make only one-click changes, then re-measure the same patch.")
    else:
        recs.append("Make the largest channel correction first, then re-measure before stacking more changes.")
    return recs[:4]


def gamma_recommendations(
    measurements: List[Measurement],
    target_gamma: float,
    peak_nits: float,
    tv_key: Optional[str] = None,
    workflow: str = "quick",
    signal_range: str = "auto",
    code_scale: str = "8bit",
    levels: Optional[Tuple[int, ...]] = None,
    eotf: str = "gamma",
) -> List[str]:
    pq = is_pq_eotf(eotf)
    # PQ has no power-law gamma; eotf_from_luminance reports tracking relative to
    # the ST.2084 reference where 1.0 == perfect, so compare against 1.0.
    effective_target = 1.0 if pq else target_gamma
    workflow_label = GAMMA_WORKFLOW_LABELS.get(workflow, workflow.title())
    rerun_message = (
        "U8G: keep the main preset fixed once close. Make Gamma Calibration moves in 5-point increments, "
        f"then rerun the full {workflow_label.lower()} pass."
        if tv_key == "u8g"
        else f"After any gamma change, run the full {workflow_label.lower()} pass again."
    )
    if not measurements:
        recs = [
            f"Run a full {workflow_label.lower()} verification pass before changing the gamma preset.",
            "For SDR, start with BT.1886. If BT.1886 is not available, use 2.2.",
        ]
        if tv_key == "u8g":
            recs.insert(1, "On the U8G, open Settings → Picture → Calibration Settings → Gamma / Gamma Calibration and start with the Gamma preset on BT.1886.")
        return recs

    gammas = []
    saw_above_knee = False
    saw_below_knee = False
    for m in measurements:
        stim_pct = gamma_nominal_pct(m, signal_range, code_scale, levels)
        if stim_pct is None:
            continue
        # Skip points whose PQ reference exceeds panel peak — firmware tone
        # mapping is active there and they would skew the aggregate advice
        # toward "too dark" forever (issue #548). Below-knee points keep normal
        # treatment so genuine midtone errors are still surfaced.
        if pq and pq_point_above_knee(stim_pct, peak_nits):
            saw_above_knee = True
            continue
        # Mark that we saw at least one non-above-knee point, regardless of
        # whether its reading succeeded.
        saw_below_knee = True
        eff_gamma = eotf_from_luminance(m.Y, peak_nits, stim_pct, eotf)
        if eff_gamma is not None:
            gammas.append({"stimulus_pct": stim_pct, "effective_gamma": eff_gamma})
    if not gammas:
        # Only attribute the empty result to the tone-mapping region when
        # every measured point was above the knee. If any below-knee point
        # was seen too, the empty result means readings failed there, and
        # blaming the wall would send the operator to raise the panel's HDR
        # peak instead of re-measuring the points that actually failed.
        if pq and saw_above_knee and not saw_below_knee:
            return [
                "All measured gamma points sit in the firmware tone-mapping region "
                "(PQ reference exceeds panel peak). Do not chase them with menu controls; "
                "re-measure with lower-stimulus patches or raise the panel's HDR peak."
            ]
        return ["Take another gamma reading. A valid effective gamma was not computed from the last pass."]

    avg = sum(m["effective_gamma"] for m in gammas) / len(gammas)
    recs = []
    if avg < effective_target - 0.15:
        if pq:
            recs.append("HDR tracking is too bright overall — midtones sit above the PQ reference. Lower the gamma/EOTF offset or use a darker tracking preset.")
        else:
            recs.append(
                "U8G: overall tracking is too bright. Switch Gamma preset to BT.1886, or lower 20%/40% Gamma Calibration controls by 5."
                if tv_key == "u8g"
                else "Tracking is too bright overall. Try a higher gamma preset or BT.1886."
            )
    elif avg > effective_target + 0.15:
        if pq:
            recs.append("HDR tracking is too dark overall — midtones sit below the PQ reference. Raise the gamma/EOTF offset or use a brighter tracking preset.")
        else:
            recs.append(
                "U8G: overall tracking is too dark. Switch Gamma preset to 2.2, or raise 20%/40% Gamma Calibration controls by 5."
                if tv_key == "u8g"
                else "Tracking is too dark overall. Try a lower gamma preset or 2.2."
            )
    else:
        if pq:
            recs.append("Overall HDR tracking is close to the PQ reference. Fine-tune only if one region is consistently off.")
        else:
            recs.append(
                "U8G: overall gamma is close. Leave the main Gamma preset and use Gamma Calibration only if one region is still off."
                if tv_key == "u8g"
                else "Overall gamma is close. Fine-tune only if one region is consistently off."
            )

    if pq:
        recs.append("HDR gamma tracking is measured against the PQ (ST.2084) reference (1.0 = perfect), not the 2.2 SDR gamma target.")

    low = [m["effective_gamma"] for m in gammas if m["stimulus_pct"] <= 40]
    high = [m["effective_gamma"] for m in gammas if m["stimulus_pct"] >= 60]
    if low and high:
        if sum(low) / len(low) < effective_target - 0.15:
            recs.append(
                "U8G: shadows too bright — lower 10%/20% Gamma Calibration controls by 5."
                if tv_key == "u8g"
                else "Shadow region is too bright. Lower black detail or use a darker gamma preset."
            )
        if sum(high) / len(high) > effective_target + 0.15:
            recs.append(
                "U8G: highlights too dark — raise 70%/80% Gamma Calibration controls by 5."
                if tv_key == "u8g"
                else "Highlight region is too dark. Raise contrast or use a lighter gamma preset."
            )

    recs.append(rerun_message)
    return recs[:4]


def u8g_gamma_control_plan(
    measurements: List[Measurement],
    target_gamma: float,
    peak_nits: float,
    signal_range: str = "auto",
    code_scale: str = "8bit",
    levels: Optional[Tuple[int, ...]] = None,
    eotf: str = "gamma",
) -> List[Dict[str, Any]]:
    # PQ tracks against the ST.2084 reference (1.0 == perfect), not target_gamma.
    pq = is_pq_eotf(eotf)
    effective_target = 1.0 if pq else target_gamma
    plan: List[Dict[str, Any]] = []
    for m in measurements:
        stim_pct = gamma_nominal_pct(m, signal_range, code_scale, levels)
        if stim_pct is None:
            continue
        stim_label = f"{round(stim_pct):.0f}%"
        eff_gamma = eotf_from_luminance(m.Y, peak_nits, stim_pct, eotf)
        # A failed/missing reading (Y <= 0 -> eff_gamma is None) is omitted
        # rather than reported as a confirmed "hold", so the operator isn't
        # told a point is fine when it was never measured.
        if eff_gamma is None:
            continue
        # PQ points whose ST.2084 reference exceeds panel peak are inside the
        # firmware tone-mapping region. The measured luminance will always read
        # as "too dark" relative to an unreachable reference, so the plan would
        # emit "raise this point" on every pass forever. Hold them instead and
        # let the operator's midtone corrections reach the knee from below
        # (issue #548 / QE_AUDIT.md tone-mapping invariant).
        if pq and pq_point_above_knee(stim_pct, peak_nits):
            plan.append(
                {
                    "control": stim_label,
                    "effective_gamma": round(eff_gamma, 3),
                    "delta": None,
                    "direction": "hold",
                    "amount": 0,
                    "summary": f"Hold {stim_label} — firmware tone-mapping region",
                    "reason": (
                        "PQ reference luminance exceeds panel peak; firmware tone "
                        "mapping is active here. Do not correct via menu controls."
                    ),
                }
            )
            continue
        delta = eff_gamma - effective_target
        if abs(delta) < 0.10:
            direction, amount, summary = "hold", 0, f"Leave {stim_label} alone"
            reason = "This gamma point is already close enough to target."
        else:
            amount = 5
            if delta > 0:
                direction = "up"
                summary = f"Raise {stim_label} by {amount}"
                reason = "Measured gamma is too high here — brighten this point."
            else:
                direction = "down"
                summary = f"Lower {stim_label} by {amount}"
                reason = "Measured gamma is too low here — darken this point."
        plan.append(
            {
                "control": stim_label,
                "effective_gamma": round(eff_gamma, 3),
                "delta": round(delta, 3),
                "direction": direction,
                "amount": amount,
                "summary": summary,
                "reason": reason,
            }
        )
    return plan


def preset_gamma_control_plan(
    measurements: List[Measurement],
    target_gamma: float,
    peak_nits: float,
    signal_range: str = "auto",
    code_scale: str = "8bit",
    levels: Optional[Tuple[int, ...]] = None,
    eotf: str = "gamma",
) -> List[Dict[str, Any]]:
    pq = is_pq_eotf(eotf)
    # PQ tracks against the ST.2084 reference (1.0 == perfect), not target_gamma.
    effective_target = 1.0 if pq else target_gamma
    if not measurements:
        return [
            {
                "control": "Gamma preset",
                "direction": "select",
                "amount": 0,
                "summary": (
                    "Select the PQ (ST.2084) tracking preset"
                    if pq
                    else f"Select the {target_gamma:.1f} preset"
                ),
                "reason": (
                    "Start with the PQ / HDR tracking preset; HDR tracks the ST.2084 reference, not a 2.2 gamma."
                    if pq
                    else f"Start with the preset closest to your target of {target_gamma:.2f}."
                ),
            }
        ]
    gammas = []
    saw_above_knee = False
    saw_below_knee = False
    for m in measurements:
        stim_pct = gamma_nominal_pct(m, signal_range, code_scale, levels)
        if stim_pct is None:
            continue
        # Exclude firmware tone-mapping points so the preset recommendation
        # isn't dragged toward "too dark" by an unreachable PQ reference
        # (issue #548).
        if pq and pq_point_above_knee(stim_pct, peak_nits):
            saw_above_knee = True
            continue
        # Mark that we saw at least one non-above-knee point, regardless of
        # whether its reading succeeded.
        saw_below_knee = True
        eff = eotf_from_luminance(m.Y, peak_nits, stim_pct, eotf)
        if eff is not None:
            gammas.append(eff)
    if not gammas:
        # Only blame the tone-mapping wall when every measured point was
        # above the knee. A below-knee point in the mix means readings
        # failed there instead, and this preset advice would misdirect the
        # operator toward raising the panel's HDR peak.
        if pq and saw_above_knee and not saw_below_knee:
            return [
                {
                    "control": "Gamma preset",
                    "direction": "hold",
                    "amount": 0,
                    "summary": "Keep current HDR tracking preset",
                    "reason": (
                        "All measured points sit in the firmware tone-mapping region "
                        "(PQ reference exceeds panel peak). Preset changes won't recover "
                        "an unreachable reference; raise the panel's HDR peak instead."
                    ),
                }
            ]
        return []
    avg = sum(gammas) / len(gammas)
    delta = avg - effective_target
    metric = "PQ tracking" if pq else "Average gamma"
    if abs(delta) < 0.10:
        return [
            {
                "control": "Gamma preset",
                "direction": "hold",
                "amount": 0,
                "summary": "Keep current gamma preset",
                "reason": f"{metric} {avg:.2f} is within 0.10 of target {effective_target:.2f}.",
            }
        ]
    if delta > 0:
        return [
            {
                "control": "Gamma preset",
                "direction": "down",
                "amount": 0,
                "summary": "Try a lower gamma preset",
                "reason": f"{metric} {avg:.2f} is above target {effective_target:.2f} — image too dark. A lower preset brightens midtones.",
            }
        ]
    return [
        {
            "control": "Gamma preset",
            "direction": "up",
            "amount": 0,
            "summary": "Try a higher gamma preset",
            "reason": f"{metric} {avg:.2f} is below target {effective_target:.2f} — image too bright. A higher preset darkens midtones.",
        }
    ]


def luminance_control_plan(
    current_nits: float,
    target_nits: float,
    validation: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = [
        {
            "control": "Brightness",
            "direction": "check",
            "amount": 0,
            "summary": "Set black level with 5% gray",
            "reason": "Raise Brightness until 5% gray is barely visible, then lower one click if blacks look lifted.",
        },
        {
            "control": "Contrast",
            "direction": "check",
            "amount": 0,
            "summary": "Set white level with 95% gray",
            "reason": "Raise Contrast until near-white detail starts to clip, then back off 1–2 clicks.",
        },
    ]
    if validation and not validation.get("valid", True):
        plan.append(
            {
                "control": "Backlight",
                "direction": "measure",
                "amount": 0,
                "summary": "Re-measure 100% white",
                "reason": validation["message"],
            }
        )
        return plan
    if current_nits <= 0:
        plan.append(
            {
                "control": "Backlight",
                "direction": "measure",
                "amount": 0,
                "summary": f"Measure 100% white toward {target_nits:.0f} nits",
                "reason": "Take a 100% white reading first to determine direction.",
            }
        )
        return plan
    diff = current_nits - target_nits
    if abs(diff) < 5:
        plan.append(
            {
                "control": "Backlight",
                "direction": "hold",
                "amount": 0,
                "summary": "Leave Backlight as-is",
                "reason": f"Peak luminance is already within 5 nits of the {target_nits:.0f}-nit target.",
            }
        )
    else:
        amount = 2 if abs(diff) >= 20 else 1
        if diff < 0:
            plan.append(
                {
                    "control": "Backlight",
                    "direction": "up",
                    "amount": amount,
                    "summary": f"Raise Backlight by {amount}",
                    "reason": f"Current reading is {abs(diff):.1f} nits below target — increase Backlight and re-measure.",
                }
            )
        else:
            plan.append(
                {
                    "control": "Backlight",
                    "direction": "down",
                    "amount": amount,
                    "summary": f"Lower Backlight by {amount}",
                    "reason": f"Current reading is {abs(diff):.1f} nits above target — decrease Backlight and re-measure.",
                }
            )
    return plan


def target_xy_for_colour(target: CalibrationTarget, colour_name: str) -> Tuple[float, float]:
    primaries = target.primaries
    xr, yr = primaries["red"]
    xg, yg = primaries["green"]
    xb, yb = primaries["blue"]
    xw, yw = target.white_point_xy
    Xr, Yr, Zr = xr / yr, 1.0, (1 - xr - yr) / yr
    Xg, Yg, Zg = xg / yg, 1.0, (1 - xg - yg) / yg
    Xb, Yb, Zb = xb / yb, 1.0, (1 - xb - yb) / yb
    M = np.array([[Xr, Xg, Xb], [Yr, Yg, Yb], [Zr, Zg, Zb]], dtype=float)
    Xw, Yw, Zw = xw / yw, 1.0, (1 - xw - yw) / yw
    S = np.linalg.solve(M, np.array([Xw, Yw, Zw], dtype=float))
    rgb_to_xyz = M @ np.diag(S)
    colour_rgb = {
        "Red": np.array([1.0, 0.0, 0.0]),
        "Green": np.array([0.0, 1.0, 0.0]),
        "Blue": np.array([0.0, 0.0, 1.0]),
        "Cyan": np.array([0.0, 1.0, 1.0]),
        "Magenta": np.array([1.0, 0.0, 1.0]),
        "Yellow": np.array([1.0, 1.0, 0.0]),
    }.get(colour_name, np.array([1.0, 1.0, 1.0]))
    X, Y, Z = rgb_to_xyz @ colour_rgb
    total = X + Y + Z
    return (round(X / total, 4), round(Y / total, 4)) if total > 0 else target.white_point_xy


def target_nits_for_colour(target: CalibrationTarget, colour_name: str) -> float:
    primaries = target.primaries
    xr, yr = primaries["red"]
    xg, yg = primaries["green"]
    xb, yb = primaries["blue"]
    xw, yw = target.white_point_xy
    Xr, Yr, Zr = xr / yr, 1.0, (1 - xr - yr) / yr
    Xg, Yg, Zg = xg / yg, 1.0, (1 - xg - yg) / yg
    Xb, Yb, Zb = xb / yb, 1.0, (1 - xb - yb) / yb
    M = np.array([[Xr, Xg, Xb], [Yr, Yg, Yb], [Zr, Zg, Zb]], dtype=float)
    Xw, Yw, Zw = xw / yw, 1.0, (1 - xw - yw) / yw
    S = np.linalg.solve(M, np.array([Xw, Yw, Zw], dtype=float))
    rgb_to_xyz = M @ np.diag(S)
    colour_rgb = {
        "Red": np.array([1.0, 0.0, 0.0]),
        "Green": np.array([0.0, 1.0, 0.0]),
        "Blue": np.array([0.0, 0.0, 1.0]),
        "Cyan": np.array([0.0, 1.0, 1.0]),
        "Magenta": np.array([1.0, 0.0, 1.0]),
        "Yellow": np.array([1.0, 1.0, 0.0]),
    }.get(colour_name, np.array([1.0, 1.0, 1.0]))
    _X, Y, _Z = rgb_to_xyz @ colour_rgb
    return round(max(0.0, Y * target.peak_luminance_nits), 1)


def cms_hints(m: Measurement, target: CalibrationTarget, colour_name: str) -> Dict[str, Any]:
    target_xy = target_xy_for_colour(target, colour_name)
    dx = m.x - target_xy[0]
    dy = m.y - target_xy[1]
    total_shift = delta_xy(m.xy, target_xy)
    wx, wy = target.white_point_xy
    target_radius = math.dist((wx, wy), target_xy)
    measured_radius = math.dist((wx, wy), m.xy)
    sat_action = (
        "Increase Saturation"
        if measured_radius < target_radius - 0.003
        else "Decrease Saturation"
        if measured_radius > target_radius + 0.003
        else "Saturation is close"
    )
    target_angle = math.atan2(target_xy[1] - wy, target_xy[0] - wx)
    measured_angle = math.atan2(m.y - wy, m.x - wx)
    angle_diff = (measured_angle - target_angle + math.pi) % (2 * math.pi) - math.pi
    hue_action = (
        "Decrease Hue"
        if angle_diff > 0.03
        else "Increase Hue"
        if angle_diff < -0.03
        else "Hue is close"
    )
    target_nits = target_nits_for_colour(target, colour_name)
    brightness_action = (
        "Increase Brightness"
        if m.Y < target_nits * 0.9
        else "Decrease Brightness"
        if m.Y > target_nits * 1.1
        else "Brightness is close"
    )
    recommendations = [
        r
        for r in [hue_action, sat_action, brightness_action]
        if not r.endswith("is close")
    ]
    if not recommendations:
        recommendations = [
            "This color is close. Re-measure once more and move to the next color."
        ]

    return {
        "target_xy": [target_xy[0], target_xy[1]],
        "target_nits": target_nits,
        "delta_xy": round(total_shift, 4),
        "hue": {"value": round(angle_diff, 4), "action": hue_action},
        "saturation": {
            "value": round(measured_radius - target_radius, 4),
            "action": sat_action,
        },
        "brightness": {
            "value": round(m.Y - target_nits, 1),
            "action": brightness_action,
        },
        "overall": "excellent" if total_shift < 0.01 else ("close" if total_shift < 0.02 else "needs_work"),
        "hold_reason": "",
        "recommendations": recommendations[:3],
    }


def cms_control_plan(
    hints: Dict[str, Any],
    cms_controls: List[str],
    colour_name: str = "",
    tv: Optional[TVProfile] = None,
) -> List[Dict[str, Any]]:
    del tv
    hue_ctrl = next((c for c in cms_controls if "hue" in c.lower()), "Hue")
    sat_ctrl = next((c for c in cms_controls if "sat" in c.lower()), "Saturation")
    lum_ctrl = next((c for c in cms_controls if any(x in c.lower() for x in ("bright", "lum"))), "Brightness")

    if hints.get("hold_reason"):
        return [
            {
                "control": f"{hue_ctrl} / {sat_ctrl} / {lum_ctrl}",
                "direction": "hold",
                "amount": 0,
                "summary": f"Leave {colour_name or 'this color'} as-is",
                "reason": hints["hold_reason"],
            }
        ]

    plan: List[Dict[str, Any]] = []
    hue_action = hints["hue"]["action"]
    if not hue_action.endswith("is close"):
        amount = 2 if abs(hints["hue"]["value"]) >= 0.06 else 1
        direction = "up" if "Increase" in hue_action else "down"
        plan.append(
            {
                "control": hue_ctrl,
                "direction": direction,
                "amount": amount,
                "summary": f"{'Raise' if direction == 'up' else 'Lower'} {hue_ctrl} by {amount}",
                "reason": "Hue is rotated away from the target angle.",
            }
        )

    sat_action = hints["saturation"]["action"]
    if not sat_action.endswith("is close"):
        amount = 2 if abs(hints["saturation"]["value"]) >= 0.01 else 1
        direction = "up" if "Increase" in sat_action else "down"
        plan.append(
            {
                "control": sat_ctrl,
                "direction": direction,
                "amount": amount,
                "summary": f"{'Raise' if direction == 'up' else 'Lower'} {sat_ctrl} by {amount}",
                "reason": "The measured color is too close to or too far from the white point.",
            }
        )

    brightness_action = hints["brightness"]["action"]
    if not brightness_action.endswith("is close"):
        amount = 2 if abs(hints["brightness"]["value"]) >= 10 else 1
        direction = "up" if "Increase" in brightness_action else "down"
        plan.append(
            {
                "control": lum_ctrl,
                "direction": direction,
                "amount": amount,
                "summary": f"{'Raise' if direction == 'up' else 'Lower'} {lum_ctrl} by {amount}",
                "reason": "This color's luminance is off target.",
            }
        )

    if not plan:
        plan.append(
            {
                "control": f"{hue_ctrl} / {sat_ctrl} / {lum_ctrl}",
                "direction": "hold",
                "amount": 0,
                "summary": "Leave this color as-is",
                "reason": "This color is already close enough. Re-measure once more and move on.",
            }
        )
    return plan[:3]
