from __future__ import annotations

from typing import Optional, Tuple

from .colour import D65_xy, xyY_to_xyz
from .eotf import bt1886_eotf, gamma_eotf, pq_eotf
from .models import AnalysisConfig, Patch
from .spaces import detect_matrix, rgb_to_xyz


def target_xyz_for_patch(
    patch: Patch,
    code_max: int,
    cfg: AnalysisConfig,
    measured_peak_y: Optional[float],
    measured_black_y: float,
) -> Tuple[float, float, float]:
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
            target_y = bt1886_eotf(n, measured_peak_y, measured_black_y, gamma=2.4)
        else:
            gamma = (
                2.2
                if cfg.eotf.lower() in ("gamma22", "2.2", "gamma")
                else float(cfg.eotf)
            )
            target_y = gamma_eotf(n, gamma=gamma) * measured_peak_y
        return xyY_to_xyz(D65_xy[0], D65_xy[1], target_y)

    matrix = detect_matrix(cfg.target_space)
    return rgb_to_xyz(target_rgb, matrix)
