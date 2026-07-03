"""Non-colour utility helpers shared across the calibrator backend."""

from __future__ import annotations

import math

from calcore.colour import ciede2000, xyY_to_xyz, xyz_to_lab
from calcore.eotf import pq_eotf, pq_inverse_eotf, pq_target_nits


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


def is_pq_eotf(eotf: str) -> bool:
    """True when the EOTF label denotes PQ / SMPTE ST.2084."""
    e = (eotf or "").lower()
    return "pq" in e or "2084" in e


# A PQ stimulus point is "above the knee" — inside the firmware tone-mapping
# region — when its absolute ST.2084 reference luminance exceeds the panel
# peak. This is a hard criterion (the reference is literally unreachable on
# this panel) so genuine midtone errors below the knee are never suppressed.
# See issue #548 and QE_AUDIT.md.


def pq_point_above_knee(stimulus_pct: float, peak_nits: float) -> bool:
    """True when the ST.2084 reference luminance at ``stimulus_pct`` exceeds
    the panel peak — i.e. firmware tone mapping is active and the point must
    not be "corrected" via menu controls (issue #548 / QE_AUDIT.md).

    ST.2084 is an absolute EOTF: the encoded signal maps directly to nits,
    not to a fraction of the display's peak. When the reference for a stimulus
    exceeds what the panel can actually produce, firmware tone mapping takes
    over and the measured luminance will always read as "too dark" relative to
    that reference. Such points must not be "corrected" via menu controls — the
    operator would chase the tone-mapping wall forever (issue #548).

    Delegates to ``calcore.eotf.pq_target_nits`` so the clipping notion lives
    in one place: a point is above the knee exactly when ``pq_target_nits``
    clips its reference (``pq_target_nits(n, peak) < pq_eotf(n)``).
    """
    if peak_nits <= 0 or stimulus_pct <= 0 or stimulus_pct >= 100:
        return False
    n = stimulus_pct / 100.0
    return pq_target_nits(n, peak_nits) < pq_eotf(n)


def eotf_from_luminance(measured_nits, peak_nits, stimulus_pct, eotf="gamma"):
    """Effective tracking exponent of a measured point under its session EOTF.

    For power-law / BT.1886 EOTFs this returns the plain effective gamma
    ``log(Y/peak) / log(stim/100)`` — compare it against the target gamma
    (e.g. 2.2 / 2.4).

    For PQ (SMPTE ST.2084) the panel does not follow a power law, so a plain
    gamma is meaningless. Instead we encode the *measured* luminance back to a
    PQ signal with the inverse ST.2084 OETF and report the exponent relative to
    the stimulus: ``log(pq_signal(Y)) / log(stim/100)``. Perfect PQ tracking
    yields 1.0 (compare against a target of 1.0, not 2.2); values above 1.0 mean
    the point is too dark, below 1.0 too bright.

    Examples:
        PQ reference at 50% stimulus is ~92.25 nits, so a display hitting that
        tracks perfectly::

            >>> round(eotf_from_luminance(92.25, 1000, 50, "PQ (ST.2084)"), 2)
            1.0

        A point measuring 250 nits at 50% is far brighter than the 92-nit
        reference, so tracking drops below 1.0::

            >>> round(eotf_from_luminance(250, 1000, 50, "PQ (ST.2084)"), 2)
            0.73

        Half the reference luminance (~46 nits) is too dark, so tracking rises
        above 1.0::

            >>> round(eotf_from_luminance(46.1, 1000, 50, "PQ (ST.2084)"), 2)
            1.21
    """
    # stimulus_pct is in (0, 100) past this guard, so normalised_in is in (0, 1).
    if stimulus_pct <= 0 or stimulus_pct >= 100 or peak_nits <= 0:
        return None
    normalised_in = stimulus_pct / 100.0
    if is_pq_eotf(eotf):
        if measured_nits <= 0:
            return None
        pq_signal = pq_inverse_eotf(measured_nits)
        if pq_signal <= 0 or pq_signal >= 1.0:
            return None
        return math.log(pq_signal) / math.log(normalised_in)
    # Power-law and BT.1886 (BT.1886 with a near-zero black floor reduces to a
    # pure power law, which is the regime SDR panels operate in).
    return gamma_from_luminance(measured_nits, peak_nits, stimulus_pct)


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


_MEASUREMENT_BUCKETS = (
    "pre_measurements",
    "wb_measurements",
    "gamma_measurements",
    "cms_measurements",
    "post_measurements",
)


def get_all_measurements(session: dict) -> list:
    """Return a flat list of all measurements across every bucket."""
    result: list = []
    for key in _MEASUREMENT_BUCKETS:
        bucket = session.get(key)
        if bucket:
            result.extend(bucket)
    return result
