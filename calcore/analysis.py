from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .colour import D65_XYZ, D65_xy, ciede2000, xyY_to_xyz, xyz_to_lab
from .eotf import bt1886_gamma_from_luminance, pq_target_nits
from .models import AnalysisConfig, Patch, Summary, _normalize_code
from .targets import target_xyz_for_patch


def mean_or_none(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.mean(values)


def max_patch(rows: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    return max(rows, key=lambda r: r.get(key, float("-inf")))


def analyze(
    patches: List[Patch],
    cfg: AnalysisConfig,
    white_point_xy: Optional[Tuple[float, float]] = None,
) -> Summary:
    """Analyze a list of measured patches against calibration targets.

    Args:
        patches: Measured color/grayscale patches from the instrument.
        cfg: Analysis configuration (mode, EOTF, target color space, code range).
        white_point_xy: Target white point chromaticity (x, y). Defaults to D65
            (0.3127, 0.3290). Used for both grayscale target XYZ computation and
            as the LAB reference white, so target and measured values are evaluated
            in the same chromatic context.
    """
    if not patches:
        raise ValueError("No valid patches found in the CSV.")

    if white_point_xy is None:
        white_point_xy = D65_xy

    # Derive an absolute XYZ reference white at Y=100 from the chromaticity.
    # xyz_to_lab expects absolute XYZ (not normalized), consistent with measured XYZ.
    wx, wy = white_point_xy
    white_xyz: Tuple[float, float, float] = xyY_to_xyz(wx, wy, 100.0)

    grayscale = [p for p in patches if p.is_grayscale]
    colors = [p for p in patches if not p.is_grayscale]

    gray_n_y = [
        (
            _normalize_code(p.r_target, cfg.signal_range) / cfg.code_max,
            p.meas_yxy[0] if p.meas_yxy is not None else p.meas_xyz[1],
        )
        for p in grayscale
    ]
    if gray_n_y:
        gray_measured_y = [y for _, y in gray_n_y]
        measured_peak_y = max(gray_measured_y)
        measured_black_y = min(gray_measured_y)
    else:
        measured_peak_y = None
        measured_black_y = 0.0

    # A black floor for the BT.1886 gamma fit must come from a patch that is
    # itself provably near-black (n <= 0.5%, matching the ``is_black_patch``
    # precedent in calibrator/session.py) — not just whichever patch happens
    # to be darkest in the current call. analyze() can be invoked with a
    # partial import (e.g. cli.py's run_once() analyzing a single CSV that
    # only contains gamma-tracking points at 20/40/60/80%, no true 0% patch),
    # in which case `measured_black_y` above is a lifted midtone rather than
    # a black floor; feeding that to the fit as `lb` biases gamma high
    # instead of low — the same failure mode issue #637 fixed, inverted.
    confirmed_black_ys = [y for n, y in gray_n_y if n <= 0.005]
    gamma_black_y = min(confirmed_black_ys) if confirmed_black_ys else 0.0

    # Define a safe default for peak luminance to prevent None values
    peak_fallback = 100.0 if cfg.mode.lower() == 'sdr' else 1000.0
    measured_peak_y_effective = measured_peak_y if measured_peak_y and measured_peak_y > 0 else peak_fallback

    grayscale_rows: List[Dict[str, Any]] = []
    color_rows: List[Dict[str, Any]] = []

    gray_des: List[float] = []
    gray_gamma_mid: List[float] = []
    gray_pq_err_mid: List[float] = []

    for p in grayscale:
        target_xyz = target_xyz_for_patch(
            p,
            cfg.code_max,
            cfg,
            measured_peak_y_effective,
            measured_black_y,
            white_point_xy,
        )
        targ_lab = xyz_to_lab(target_xyz, white_xyz)
        meas_lab = xyz_to_lab(p.meas_xyz, white_xyz)
        de = ciede2000(targ_lab, meas_lab)
        gray_des.append(de)

        n = _normalize_code(p.r_target, cfg.signal_range) / cfg.code_max
        meas_y = p.meas_yxy[0] if p.meas_yxy is not None else p.meas_xyz[1]
        gamma_val = None
        pq_err_pct = None
        if 0 < n < 1 and meas_y > 0 and measured_peak_y_effective > 0:
            if cfg.mode.lower() == "hdr" or cfg.eotf.lower() == "pq":
                # PQ is absolute: compare measured nits directly against the
                # (peak-clipped) absolute ST.2084 target, not a peak-relative
                # rescaling of it.
                target_y = pq_target_nits(n, measured_peak_y_effective)
                if target_y > 0:
                    pq_err_pct = 100.0 * (meas_y - target_y) / target_y
                    if 0.20 <= n <= 0.80:
                        gray_pq_err_mid.append(pq_err_pct)
            else:
                if 0 < meas_y < measured_peak_y_effective:
                    # BT.1886-aware fit: a through-origin power law assumes
                    # Y(0) == 0, but real SDR panels have a non-zero measured
                    # black floor that flattens the low end of the curve and
                    # biases a naive log/log fit low (issue #637). Feed the
                    # panel's own measured black level in so a perfect
                    # BT.1886 tracker reports its true gamma regardless of
                    # black level.
                    gamma_val = bt1886_gamma_from_luminance(
                        meas_y, measured_peak_y_effective, n, gamma_black_y
                    )
                    if gamma_val is not None and 0.20 <= n <= 0.80:
                        gray_gamma_mid.append(gamma_val)

        grayscale_rows.append(
            {
                "label": p.label,
                "target_xyz": target_xyz,
                "measured_xyz": p.meas_xyz,
                "dE2000": de,
                "gamma": gamma_val,
                "pq_error_pct": pq_err_pct,
            }
        )

    color_stats: Dict[str, Dict[str, List[float]]] = {
        "75": {"de": [], "chroma": []},
        "100": {"de": [], "chroma": []},
        "other": {"de": [], "chroma": []},
    }

    for p in colors:
        target_xyz = target_xyz_for_patch(
            p,
            cfg.code_max,
            cfg,
            measured_peak_y_effective,
            measured_black_y,
            white_point_xy,
        )
        targ_lab = xyz_to_lab(target_xyz, white_xyz)
        meas_lab = xyz_to_lab(p.meas_xyz, white_xyz)
        de = ciede2000(targ_lab, meas_lab)

        # Chroma-only approximation: keep measured L* fixed while comparing chromaticity mismatch.
        targ_chroma_lab = (meas_lab[0], targ_lab[1], targ_lab[2])
        de_chroma = ciede2000(targ_chroma_lab, meas_lab)

        bucket = p.sat_bucket(cfg.code_max, cfg.signal_range)
        color_stats[bucket]["de"].append(de)
        color_stats[bucket]["chroma"].append(de_chroma)

        color_rows.append(
            {
                "label": p.label,
                "bucket": bucket,
                "target_xyz": target_xyz,
                "measured_xyz": p.meas_xyz,
                "dE2000": de,
                "dE2000_chroma_only": de_chroma,
            }
        )

    return Summary(
        grayscale_avg_de=mean_or_none(gray_des),
        grayscale_max_de=max(gray_des) if gray_des else None,
        grayscale_over_3=sum(1 for d in gray_des if d > 3.0),
        gamma_midtones=mean_or_none(gray_gamma_mid),
        pq_err_midtones=mean_or_none(gray_pq_err_mid),
        color_75_avg_de=mean_or_none(color_stats.get("75", {}).get("de", [])),
        color_75_max_de=(
            max(color_stats.get("75", {}).get("de", []))
            if color_stats.get("75", {}).get("de")
            else None
        ),
        color_75_chroma_avg=mean_or_none(color_stats.get("75", {}).get("chroma", [])),
        color_100_avg_de=mean_or_none(color_stats.get("100", {}).get("de", [])),
        color_100_max_de=(
            max(color_stats.get("100", {}).get("de", []))
            if color_stats.get("100", {}).get("de")
            else None
        ),
        color_100_chroma_avg=mean_or_none(
            color_stats.get("100", {}).get("chroma", [])
        ),
        grayscale_rows=grayscale_rows,
        color_rows=color_rows,
        meta={
            "patch_count": len(patches),
            "grayscale_count": len(grayscale),
            "color_count": len(colors),
            "measured_peak_y": measured_peak_y,
            "measured_black_y": measured_black_y,
            "mode": cfg.mode,
            "eotf": cfg.eotf,
            "target_space": cfg.target_space,
            "code_max": cfg.code_max,
            "peak_fallback_used": not (measured_peak_y and measured_peak_y > 0),
        },
        measured_patch_count=len(patches),
    )
