from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence

from .colour import D65_xy, ciede2000, xyz_to_lab
from .eotf import pq_eotf
from .models import AnalysisConfig, Patch, Summary
from .targets import target_xyz_for_patch


def mean_or_none(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.mean(values)


def max_patch(rows: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    return max(rows, key=lambda r: r.get(key, float("-inf")))


def analyze(patches: List[Patch], cfg: AnalysisConfig, white_point_xy=None) -> Summary:
    if not patches:
        raise ValueError("No valid patches found in the CSV.")

    if white_point_xy is None:
        white_point_xy = D65_xy

    grayscale = [p for p in patches if p.is_grayscale]
    colors = [p for p in patches if not p.is_grayscale]

    gray_measured_y = [
        p.meas_yxy[0] if p.meas_yxy is not None else p.meas_xyz[1]
        for p in grayscale
    ]
    if gray_measured_y:
        measured_peak_y = max(gray_measured_y)
        measured_black_y = min(gray_measured_y)
    else:
        measured_peak_y = None
        measured_black_y = 0.0

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
        targ_lab = xyz_to_lab(target_xyz)
        meas_lab = xyz_to_lab(p.meas_xyz)
        de = ciede2000(targ_lab, meas_lab)
        gray_des.append(de)

        n = p.r_target / cfg.code_max
        meas_y = p.meas_yxy[0] if p.meas_yxy is not None else p.meas_xyz[1]
        gamma_val = None
        pq_err_pct = None
        if 0 < n < 1 and meas_y > 0 and measured_peak_y_effective > 0:
            if cfg.mode.lower() == "hdr" or cfg.eotf.lower() == "pq":
                target_rel = pq_eotf(n) / 10000.0
                meas_rel = meas_y / measured_peak_y_effective
                pq_err_pct = 100.0 * (meas_rel - target_rel)
                if 0.20 <= n <= 0.80:
                    gray_pq_err_mid.append(pq_err_pct)
            else:
                if 0 < meas_y < measured_peak_y_effective:
                    rel = meas_y / measured_peak_y_effective
                    gamma_val = math.log(rel) / math.log(n)
                    if 0.20 <= n <= 0.80:
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
        targ_lab = xyz_to_lab(target_xyz)
        meas_lab = xyz_to_lab(p.meas_xyz)
        de = ciede2000(targ_lab, meas_lab)

        # Chroma-only approximation: keep measured L* fixed while comparing chromaticity mismatch.
        targ_chroma_lab = (meas_lab[0], targ_lab[1], targ_lab[2])
        de_chroma = ciede2000(targ_chroma_lab, meas_lab)

        bucket = p.sat_bucket
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
