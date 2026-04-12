"""Non-colour utility helpers shared across the calibrator backend."""

from __future__ import annotations

import math

from calcore.colour import ciede2000, xyY_to_xyz, xyz_to_lab


def delta_xy(measured_xy, target_xy) -> float:
    return math.sqrt(
        (measured_xy[0] - target_xy[0]) ** 2 + (measured_xy[1] - target_xy[1]) ** 2
    )


def stimulus_pct_from_code_value(value: int, signal_range: str = "auto") -> float:
    if signal_range == "auto":
        if value > 255:
            signal_range = "full10"
        elif value <= 16:
            return 0.0
        elif value >= 235:
            return 100.0
        else:
            full_range_pct = value / 255.0 * 100.0
            legal_range_pct = (value - 16) / (235 - 16) * 100.0
            snap_levels = range(0, 101, 5)

            def snap_error(pct: float) -> float:
                return min(abs(pct - level) for level in snap_levels)

            chosen_pct = (
                legal_range_pct
                if snap_error(legal_range_pct) < snap_error(full_range_pct)
                else full_range_pct
            )
            return round(chosen_pct, 1)
    if signal_range == "full10":
        return round(min(100.0, max(0.0, value / 1023.0 * 100.0)), 1)
    if signal_range == "full":
        return round(min(100.0, max(0.0, value / 255.0 * 100.0)), 1)
    if signal_range == "limited":
        if value <= 16:
            return 0.0
        if value >= 235:
            return 100.0
        return round((value - 16) / (235 - 16) * 100.0, 1)
    return 0.0


def gamma_from_luminance(measured_nits, peak_nits, stimulus_pct):
    if stimulus_pct <= 0 or stimulus_pct >= 100 or peak_nits <= 0:
        return None
    normalised_out = measured_nits / peak_nits
    normalised_in = stimulus_pct / 100.0
    if normalised_out <= 0 or normalised_in <= 0 or normalised_out > 1.0:
        return None
    return math.log(normalised_out) / math.log(normalised_in)


def delta_e_ciede2000_xyY(
    x1: float,
    y1: float,
    Y1: float,
    x2: float,
    y2: float,
    Y2: float,
) -> float:
    lab1 = xyz_to_lab(xyY_to_xyz(x1, y1, Y1))
    lab2 = xyz_to_lab(xyY_to_xyz(x2, y2, Y2))
    return ciede2000(lab1, lab2)


def rating_emoji(delta_e: float) -> str:
    if delta_e <= 1.0:
        return "[bold green]★★★ Excellent[/bold green]"
    if delta_e <= 2.0:
        return "[green]★★☆ Good[/green]"
    if delta_e <= 3.0:
        return "[yellow]★☆☆ Acceptable[/yellow]"
    return "[red]✗ Needs work[/red]"


def direction_hint(measured_val, target_val, label="") -> str:
    diff = measured_val - target_val
    if abs(diff) < 0.001:
        return "[green]On target[/green]"
    if diff > 0:
        return f"[yellow]↓ Decrease {label}[/yellow]"
    return f"[cyan]↑ Increase {label}[/cyan]"
