"""
ZRO Calibration Helper package.

All public symbols are re-exported here.
Import from the submodules directly for cleaner dependency graphs.
"""

from .colour import (
    REQUIRED_PACKAGES, ensure_packages, console,
    D65_XY, D65_XYZ,
    REC709_PRIMARIES, P3_PRIMARIES, REC2020_PRIMARIES,
    GRAYSCALE_LEVELS, SATURATION_LEVELS, COLOUR_TARGETS,
    GAMMA_SDR, GAMMA_HDR_PQ,
    CalMode, Measurement, CalibrationTarget, CalibrationReport,
    SDR_TARGET, HDR10_TARGET, DV_TARGET,
    delta_e_cie76, xyY_to_XYZ, XYZ_to_lab, xyY_to_lab,
    delta_xy, gamma_from_luminance, rating_emoji, direction_hint,
    stimulus_pct_from_code_value,
)
from .profiles import TVProfile, TV_PROFILES, DEFAULT_TV_PROFILE
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
from .zro_import import parse_zro_csv, merge_into_session, ZROImportResult

__all__ = [
    "REQUIRED_PACKAGES", "ensure_packages", "console",
    "D65_XY", "D65_XYZ",
    "REC709_PRIMARIES", "P3_PRIMARIES", "REC2020_PRIMARIES",
    "GRAYSCALE_LEVELS", "SATURATION_LEVELS", "COLOUR_TARGETS",
    "GAMMA_SDR", "GAMMA_HDR_PQ",
    "CalMode", "Measurement", "CalibrationTarget", "CalibrationReport",
    "SDR_TARGET", "HDR10_TARGET", "DV_TARGET",
    "delta_e_cie76", "xyY_to_XYZ", "XYZ_to_lab", "xyY_to_lab",
    "delta_xy", "gamma_from_luminance", "rating_emoji", "direction_hint",
    "stimulus_pct_from_code_value",
    "TVProfile", "TV_PROFILES", "DEFAULT_TV_PROFILE",
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
