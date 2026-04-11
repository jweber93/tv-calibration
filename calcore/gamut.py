"""Gamut feasibility check — per-primary chromaticity constraint analysis.

Determines how far each measured color primary sits from the target color space,
whether the ADB CMS range can cover the correction, and what compromise to offer
when it cannot.  The output is passed to the LLM (via llm.query_gamut_advice) so
it can articulate the trade-off in plain English for the user to accept or override.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .spaces import BT709_PRIMARIES, BT2020_PRIMARIES, P3D65_PRIMARIES
from .colour import D65_xy


# Conservative ADB CMS range estimates derived from empirical Hisense U8G data.
# One click ≈ 0.003–0.005 xy shift at primary distance; ±10 clicks budget.
_ADB_HUE_RANGE_DEG = 8.0   # degrees of hue correction available
_ADB_SAT_RANGE_PCT = 20.0  # percent chroma change available

_PRIMARY_NAMES = ("Red", "Green", "Blue", "Cyan", "Magenta", "Yellow")


@dataclass
class PrimaryConstraint:
    """Constraint analysis for one color primary or secondary."""
    primary: str
    native_xy: Tuple[float, float]         # measured chromaticity
    target_xy: Tuple[float, float]         # target space chromaticity
    chroma_excess_pct: float               # +ve = over-saturated vs target
    hue_error_deg: float                   # +ve = CW rotation from target angle
    within_adb_range: bool                 # True if both hue & sat are correctable
    sat_correction_pct: float              # additional sat reduction needed beyond ADB range
    luma_impact_pct: float                 # estimated luminance cost of full correction
    de_approx: float                       # rough chromaticity-only ΔE estimate


@dataclass
class GamutDiagnosis:
    """Full gamut feasibility report for one calibration session."""
    target_space: str
    constraints: List[PrimaryConstraint] = field(default_factory=list)
    unreachable_primaries: List[str] = field(default_factory=list)
    recommended_compromises: List[str] = field(default_factory=list)


def _target_primaries(target_space: str) -> Dict[str, Tuple[float, float]]:
    """Return {Name: (x, y)} for primaries + secondaries in the given space."""
    key = target_space.lower()
    if "2020" in key:
        base = dict(BT2020_PRIMARIES)
    elif "p3" in key:
        base = dict(P3D65_PRIMARIES)
    else:
        base = dict(BT709_PRIMARIES)

    r, g, b = base["red"], base["green"], base["blue"]
    return {
        "Red": r,
        "Green": g,
        "Blue": b,
        # Secondaries approximated as chromaticity midpoints — close enough for
        # feasibility checking; real CMS targets are computed by guidance.py.
        "Cyan": ((g[0] + b[0]) / 2, (g[1] + b[1]) / 2),
        "Magenta": ((r[0] + b[0]) / 2, (r[1] + b[1]) / 2),
        "Yellow": ((r[0] + g[0]) / 2, (r[1] + g[1]) / 2),
    }


def _extract_primary(label: str) -> Optional[str]:
    for name in _PRIMARY_NAMES:
        if name.lower() in label.lower():
            return name
    return None


def _chroma_de(native_xy: Tuple[float, float], target_xy: Tuple[float, float]) -> float:
    """Rough perceptual distance using CIE u'v' (fast approximation)."""
    def to_uv(x: float, y: float) -> Tuple[float, float]:
        denom = -2 * x + 12 * y + 3
        if denom == 0:
            return (0.0, 0.0)
        return (4 * x / denom, 9 * y / denom)

    u1, v1 = to_uv(*native_xy)
    u2, v2 = to_uv(*target_xy)
    return math.sqrt((u1 - u2) ** 2 + (v1 - v2) ** 2) * 100  # scale to ΔE-ish units


def assess_gamut_constraints(
    color_rows: List[Dict[str, Any]],
    target_space: str,
    white_xy: Tuple[float, float] = D65_xy,
) -> GamutDiagnosis:
    """Analyse each measured color primary against the target color space.

    Args:
        color_rows:    Summary.color_rows from calcore.analysis.analyze().
        target_space:  One of "bt709", "p3d65", "bt2020".
        white_xy:      Display white point chromaticity (default D65).

    Returns a GamutDiagnosis with per-primary PrimaryConstraint objects and
    a list of primaries that are outside the ADB CMS correction range.
    """
    targets = _target_primaries(target_space)
    wx, wy = white_xy

    constraints: List[PrimaryConstraint] = []
    seen: set = set()

    for row in color_rows:
        label = row.get("label", "")
        primary_name = _extract_primary(label)
        if primary_name is None or primary_name in seen:
            continue
        if primary_name not in targets:
            continue

        meas_xyz = row.get("measured_xyz")
        if not meas_xyz:
            continue
        X, Y, Z = meas_xyz
        if Y <= 0:
            continue
        total = X + Y + Z
        if total <= 0:
            continue

        native_xy: Tuple[float, float] = (X / total, Y / total)
        target_xy = targets[primary_name]

        # Angular deviation from white point (hue error in degrees)
        target_angle = math.atan2(target_xy[1] - wy, target_xy[0] - wx)
        native_angle = math.atan2(native_xy[1] - wy, native_xy[0] - wx)
        raw_diff = (native_angle - target_angle + math.pi) % (2 * math.pi) - math.pi
        hue_error_deg = math.degrees(raw_diff)

        # Chroma excess: radial distance from white relative to target
        target_radius = math.dist((wx, wy), target_xy)
        native_radius = math.dist((wx, wy), native_xy)
        if target_radius > 0:
            chroma_excess_pct = 100.0 * (native_radius - target_radius) / target_radius
        else:
            chroma_excess_pct = 0.0

        # Can the ADB CMS range cover this deviation?
        within_adb_range = (
            abs(hue_error_deg) <= _ADB_HUE_RANGE_DEG
            and abs(chroma_excess_pct) <= _ADB_SAT_RANGE_PCT
        )

        # Extra saturation reduction needed if outside ADB range
        sat_correction_pct = max(0.0, abs(chroma_excess_pct) - _ADB_SAT_RANGE_PCT)
        # Rule-of-thumb: each 1% chroma reduction costs ~0.5% luminance
        luma_impact_pct = sat_correction_pct * 0.5

        de_approx = _chroma_de(native_xy, target_xy)

        constraints.append(
            PrimaryConstraint(
                primary=primary_name,
                native_xy=(round(native_xy[0], 4), round(native_xy[1], 4)),
                target_xy=(round(target_xy[0], 4), round(target_xy[1], 4)),
                chroma_excess_pct=round(chroma_excess_pct, 1),
                hue_error_deg=round(hue_error_deg, 1),
                within_adb_range=within_adb_range,
                sat_correction_pct=round(sat_correction_pct, 1),
                luma_impact_pct=round(luma_impact_pct, 1),
                de_approx=round(de_approx, 2),
            )
        )
        seen.add(primary_name)

    unreachable = [c.primary for c in constraints if not c.within_adb_range]
    recommended_compromises = [
        f"{c.primary.lower()}_reduced_sat"
        for c in constraints
        if not c.within_adb_range and c.chroma_excess_pct > 0
    ]

    return GamutDiagnosis(
        target_space=target_space,
        constraints=constraints,
        unreachable_primaries=unreachable,
        recommended_compromises=recommended_compromises,
    )


def format_gamut_diagnosis(diagnosis: GamutDiagnosis) -> str:
    """Format a GamutDiagnosis as a human-readable (and LLM-readable) text block."""
    lines = [f"Target space: {diagnosis.target_space}"]
    lines.append("")

    for c in diagnosis.constraints:
        status = "WITHIN ADB RANGE" if c.within_adb_range else "OUTSIDE ADB RANGE"
        lines.append(
            f"{c.primary}: native ({c.native_xy[0]:.4f}, {c.native_xy[1]:.4f}) "
            f"vs target ({c.target_xy[0]:.4f}, {c.target_xy[1]:.4f}) — {status}"
        )
        lines.append(
            f"  Hue error: {c.hue_error_deg:+.1f}°  |  "
            f"Chroma: {c.chroma_excess_pct:+.1f}%  |  "
            f"ΔE≈{c.de_approx:.1f}"
        )
        if not c.within_adb_range:
            lines.append(
                f"  → Correction requires sat reduction of {c.sat_correction_pct:.1f}% "
                f"beyond ADB range (est. luminance cost: {c.luma_impact_pct:.1f}%)"
            )
    if diagnosis.unreachable_primaries:
        lines.append("")
        lines.append(f"Primaries outside ADB correction range: {', '.join(diagnosis.unreachable_primaries)}")
    if diagnosis.recommended_compromises:
        lines.append(f"Suggested compromises: {', '.join(diagnosis.recommended_compromises)}")

    return "\n".join(lines)


def gamut_diagnosis_to_dict(diagnosis: GamutDiagnosis) -> Dict[str, Any]:
    """Serialise a GamutDiagnosis to a JSON-safe dict for API responses."""
    return {
        "target_space": diagnosis.target_space,
        "constraints": [asdict(c) for c in diagnosis.constraints],
        "unreachable_primaries": diagnosis.unreachable_primaries,
        "recommended_compromises": diagnosis.recommended_compromises,
    }
