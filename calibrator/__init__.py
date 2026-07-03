"""
ZRO Calibration Helper package.

Most colour-science primitives now come from `calcore`; this package re-exports
the stable pieces the FastAPI backend and tests use.
"""

from calcore import (
    BT2020_PRIMARIES as REC2020_PRIMARIES,
    BT709_PRIMARIES as REC709_PRIMARIES,
    CalMode,
    CalibrationTarget,
    D65_XY,
    D65_XYZ,
    DV_TARGET,
    HDR10_TARGET,
    Measurement,
    P3D65_PRIMARIES as P3_PRIMARIES,
    SDR_TARGET,
    TVSettings,
    XYZ_to_lab,
    ciede2000,
    delta_e_cie76,
    xyY_to_XYZ,
    xyY_to_lab,
)
from .models import CalibrationReport
from .profiles import TVProfile, TV_PROFILES, DEFAULT_TV_PROFILE, get_tv_profile
from .runtime import REQUIRED_PACKAGES, console, ensure_packages
from .guidance import (
    cms_control_plan,
    cms_hints,
    gamma_recommendations,
    luminance_control_plan,
    preset_gamma_control_plan,
    target_nits_for_colour,
    target_xy_for_colour,
    u8g_gamma_control_plan,
    wb_control_plan,
    wb_hints,
    wb_recommendations,
)
from .quality import step_quality
from .reports import report_payload, render_report_html
from .session import SessionStore
from .utils import (
    delta_e_ciede2000_xyY,
    delta_xy,
    direction_hint,
    eotf_from_luminance,
    gamma_from_luminance,
    is_pq_eotf,
    pq_point_above_knee,
    rating_emoji,
    stimulus_pct_from_code_value,
)
from .zro_import import parse_zro_csv, merge_into_session, ZROImportResult

GRAYSCALE_LEVELS = list(range(0, 101, 5))
SATURATION_LEVELS = [25, 50, 75, 100]
COLOUR_TARGETS = ["red", "green", "blue", "cyan", "magenta", "yellow"]
GAMMA_SDR = 2.2
GAMMA_HDR_PQ = "PQ"

__all__ = [
    "REQUIRED_PACKAGES", "ensure_packages", "console",
    "D65_XY", "D65_XYZ",
    "REC709_PRIMARIES", "P3_PRIMARIES", "REC2020_PRIMARIES",
    "GRAYSCALE_LEVELS", "SATURATION_LEVELS", "COLOUR_TARGETS",
    "GAMMA_SDR", "GAMMA_HDR_PQ",
    "CalMode", "Measurement", "CalibrationTarget", "CalibrationReport",
    "SDR_TARGET", "HDR10_TARGET", "DV_TARGET",
    "ciede2000", "delta_e_cie76", "delta_e_ciede2000_xyY", "xyY_to_XYZ", "XYZ_to_lab", "xyY_to_lab",
    "delta_xy", "gamma_from_luminance", "eotf_from_luminance", "is_pq_eotf",
    "pq_point_above_knee",
    "rating_emoji", "direction_hint",
    "stimulus_pct_from_code_value",
    "TVProfile", "TV_PROFILES", "DEFAULT_TV_PROFILE", "get_tv_profile",
    "TVSettings",
    "wb_hints", "wb_control_plan", "wb_recommendations",
    "gamma_recommendations", "u8g_gamma_control_plan", "preset_gamma_control_plan",
    "luminance_control_plan",
    "target_xy_for_colour", "target_nits_for_colour",
    "cms_hints", "cms_control_plan",
    "step_quality",
    "SessionStore",
    "report_payload", "render_report_html",
    "parse_zro_csv", "merge_into_session", "ZROImportResult",
]
