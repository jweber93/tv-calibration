import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from calcore.models import Measurement, Patch
from calibrator.session import ZROImportResult, CalibrationTarget
from calibrator.utils import stimulus_pct_from_code_value

logger = logging.getLogger(__name__)

# Grayscale stimulus levels used by the calibration workflow (% of signal range)
WB_GAIN_TARGET_PCT = 80.0
WB_OFFSET_TARGET_PCT = 30.0
GAMMA_TARGET_PCTS = tuple(float(pct) for pct in range(5, 100, 5))
PCT_SNAP_TOLERANCE = 7.0

# Session steps that should route grayscale to pre/post buckets
_PRE_STEPS = {"pre_grayscale", "prepare", "select_mode"}
_POST_STEPS = {"post_grayscale", "suggested_patches", "report"}


def _nearest_target_pct(stim: float, targets: tuple) -> Optional[float]:
    matches = [t for t in targets if abs(stim - t) <= PCT_SNAP_TOLERANCE]
    if not matches:
        return None
    return min(matches, key=lambda t: abs(stim - t))


def _stimulus_pct_from_patch(patch: Patch, signal_range: str = "auto") -> float:
    """Derive stimulus percentage from a grayscale patch's RGB target values."""
    if not patch.is_grayscale:
        return 0.0
    val = patch.r_target  # r == g == b for grayscale
    return stimulus_pct_from_code_value(val, signal_range)


def _bucket_for_grayscale(stim_pct: float, session_step: Optional[str]) -> List[str]:
    """
    Return the list of bucket names a grayscale patch belongs to.

    A single patch can populate multiple buckets (e.g. 80% gray goes to both
    wb_measurements and gamma_measurements), plus the primary grayscale bucket.
    """
    buckets: List[str] = []

    # Primary grayscale bucket based on session step
    if session_step in _PRE_STEPS:
        buckets.append("pre_measurements")
    elif session_step in _POST_STEPS:
        buckets.append("post_measurements")
    else:
        # No session step or mid-workflow: fall back to lum_measurements
        buckets.append("lum_measurements")

    # Luminance reference: 100% white
    if stim_pct >= 99.0:
        buckets.append("lum_measurements")

    # White balance: 80% gain, 30% offset
    if _nearest_target_pct(stim_pct, (WB_GAIN_TARGET_PCT, WB_OFFSET_TARGET_PCT)) is not None:
        buckets.append("wb_measurements")

    # Gamma tracking: snap to nearest 5% step
    if _nearest_target_pct(stim_pct, GAMMA_TARGET_PCTS) is not None:
        buckets.append("gamma_measurements")

    # Deduplicate while preserving order (e.g. lum_measurements may be added twice)
    return list(dict.fromkeys(buckets))


def patches_to_session_buckets(
    patches: List[Patch],
    target: CalibrationTarget,
    session_step: Optional[str] = None,
    signal_range: str = "auto",
    code_scale: str = "8bit",
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Convert a list of generic measurement patches into the session bucket structure.

    Args:
        patches: List of Patch objects to route
        target: Calibration target configuration
        session_step: Current step in calibration workflow (e.g., "pre_grayscale", "post_grayscale")
        signal_range: Signal range for stimulus calculation ("auto", "full"; default "auto")
        code_scale: Code scale for stimulus calculation ("8bit", "10bit"; default "8bit")
            Note: when signal_range="full" and code_scale="10bit", stimulus percentages are
            recontextualized using decode_range="full10"

    Mapping logic:
    - Color patches go to cms_measurements.
    - Grayscale patches are routed by stimulus level:
        * 100% white (>=99%) -> lum_measurements
        * ~80% gray -> wb_measurements (Gain)
        * ~30% gray -> wb_measurements (Offset)
        * 5-95% in 5% steps -> gamma_measurements
      Additionally, the session step determines whether grayscale patches land
      in pre_measurements (pre-calibration) or post_measurements (post-calibration).
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "pre_measurements": [],
        "cms_measurements": [],
        "lum_measurements": [],
        "wb_measurements": [],
        "gamma_measurements": [],
        "post_measurements": [],
    }

    now_iso = datetime.now(timezone.utc).isoformat()
    decode_range = "full10" if (signal_range == "full" and code_scale == "10bit") else signal_range

    for patch in patches:
        if not patch.is_grayscale:
            buckets["cms_measurements"].append(
                _build_measurement_dict(patch, now_iso)
            )
        else:
            stim_pct = _stimulus_pct_from_patch(patch, decode_range)
            patch_buckets = _bucket_for_grayscale(stim_pct, session_step)
            meas_dict = _build_measurement_dict(patch, now_iso)
            for bucket in patch_buckets:
                buckets[bucket].append(meas_dict)

    # Log for debugging
    for bucket, items in buckets.items():
        if items:
            logger.debug(
                f"patches_to_session_buckets: assigned {len(items)} patches to {bucket}"
            )

    return buckets


def _build_measurement_dict(patch: Patch, now_iso: str) -> Dict[str, Any]:
    """Build a measurement dict from a Patch object."""
    X, Y, Z = patch.meas_xyz
    if patch.meas_yxy is not None:
        Y_yxy, x, y = patch.meas_yxy
        Y = Y_yxy
    else:
        sum_xyz = X + Y + Z
        if sum_xyz > 0:
            x = X / sum_xyz
            y = Y / sum_xyz
        else:
            x = y = 0.0

    return {
        "X": X,
        "Y": Y,
        "Z": Z,
        "x": x,
        "y": y,
        "timestamp": now_iso,
        "label": patch.label,
        "stimulus_rgb": [patch.r_target, patch.g_target, patch.b_target],
    }
