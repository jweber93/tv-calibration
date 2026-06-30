from __future__ import annotations

import logging
from typing import Optional, Tuple

from .colour import D65_xy, xyY_to_xyz
from .eotf import bt1886_eotf, gamma_eotf, pq_eotf
from .models import AnalysisConfig, Patch
from .spaces import detect_matrix, rgb_to_xyz

logger = logging.getLogger(__name__)


def target_xyz_for_patch(
    patch: Patch,
    code_max: int,
    cfg: AnalysisConfig,
    measured_peak_y: Optional[float],
    measured_black_y: float,
    white_point_xy: Tuple[float, float] = D65_xy,
) -> Tuple[float, float, float]:
    if code_max <= 0:
        raise ValueError(f"Invalid code_max: {code_max}, must be a positive integer")

    target_rgb = (
        patch.r_target / code_max,
        patch.g_target / code_max,
        patch.b_target / code_max,
    )

    if patch.is_grayscale:
        n = patch.r_target / code_max
        if cfg.mode.lower() == "hdr" or cfg.eotf.lower() == "pq":
            rel = pq_eotf(n) / 10000.0
            target_y = measured_peak_y * rel
        elif cfg.eotf.lower() == "bt1886":
            # Use target's gamma: 2.2 for SDR (per SDR_TARGET in models.py), 2.4 for others
            gamma = 2.2 if cfg.mode.lower() == "sdr" else 2.4
            target_y = bt1886_eotf(n, measured_peak_y, measured_black_y, gamma=gamma)
        else:
            gamma = (
                2.2
                if cfg.eotf.lower() in ("gamma22", "2.2", "gamma")
                else float(cfg.eotf)
            )
            target_y = gamma_eotf(n, gamma=gamma) * measured_peak_y
        return xyY_to_xyz(white_point_xy[0], white_point_xy[1], target_y)

    matrix = detect_matrix(cfg.target_space)
    return rgb_to_xyz(target_rgb, matrix)
