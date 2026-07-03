from __future__ import annotations

import logging
from typing import Optional, Tuple

from .colour import D65_xy, xyY_to_xyz
from .eotf import bt1886_eotf, gamma_eotf, pq_target_nits
from .models import AnalysisConfig, Patch, _normalize_code
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
    """Compute the ideal target XYZ for a single patch.

    For grayscale patches, applies the session EOTF to derive target luminance Y,
    then converts to XYZ using white_point_xy as the chromaticity (default D65).
    For color patches, converts the target RGB to XYZ via the target color space matrix.

    Args:
        white_point_xy: Chromaticity (x, y) of the target white point. Defaults to
            D65 (0.3127, 0.3290). Pass the session target's white_point_xy for
            non-D65 calibrations so grayscale targets are placed correctly.
    """
    if code_max <= 0:
        raise ValueError(f"Invalid code_max: {code_max}, must be a positive integer")

    target_rgb = (
        _normalize_code(patch.r_target, cfg.signal_range) / code_max,
        _normalize_code(patch.g_target, cfg.signal_range) / code_max,
        _normalize_code(patch.b_target, cfg.signal_range) / code_max,
    )

    if patch.is_grayscale:
        n = _normalize_code(patch.r_target, cfg.signal_range) / code_max
        if cfg.mode.lower() == "hdr" or cfg.eotf.lower() == "pq":
            # PQ (ST.2084) is an absolute EOTF: the signal encodes nits
            # directly, not a fraction of peak. Clip at the display's
            # measured peak (its tone-map knee).
            target_y = pq_target_nits(n, measured_peak_y)
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
