import logging
from typing import List, Dict, Any
from datetime import datetime

from calcore.models import Measurement, Patch
from calibrator.session import ZROImportResult, CalibrationTarget

logger = logging.getLogger(__name__)

def patches_to_session_buckets(patches: List[Patch], target: CalibrationTarget) -> Dict[str, List[Dict[str, Any]]]:
    """
    Convert a list of generic measurement patches into the session bucket structure.

    Mapping logic:
    - Grayscale patches (r=g=b) go to lum_measurements.
    - Color patches go to cms_measurements.

    This is a simple heuristic; a more sophisticated approach could consider the
    session step and target configuration.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "pre_measurements": [],
        "cms_measurements": [],
        "lum_measurements": [],
        "wb_measurements": [],
        "gamma_measurements": [],
        "post_measurements": [],
    }

    now_iso = datetime.now().isoformat()

    for patch in patches:
        # Determine which bucket to use
        if patch.is_grayscale:
            bucket = "lum_measurements"
        else:
            bucket = "cms_measurements"

        # Convert meas_xyz and meas_yxy to Measurement fields
        # Patch.meas_xyz is (X, Y, Z)
        # Patch.meas_yxy is (Y, x, y) if available
        X, Y, Z = patch.meas_xyz
        if patch.meas_yxy is not None:
            Y_yxy, x, y = patch.meas_yxy
            # Y may differ; use Y from meas_yxy? We'll keep Y from meas_yxy for consistency
            Y = Y_yxy
        else:
            # Compute x, y from X, Y, Z
            sum_xyz = X + Y + Z
            if sum_xyz > 0:
                x = X / sum_xyz
                y = Y / sum_xyz
            else:
                x = y = 0.0

        # Build dict matching Measurement fields
        meas_dict = {
            "X": X,
            "Y": Y,
            "Z": Z,
            "x": x,
            "y": y,
            "timestamp": now_iso,
            "label": patch.label,
            "stimulus_rgb": [patch.r_target, patch.g_target, patch.b_target],
        }

        buckets[bucket].append(meas_dict)

    # Log for debugging
    for bucket, items in buckets.items():
        if items:
            logger.debug(f"patches_to_session_buckets: assigned {len(items)} patches to {bucket}")

    return buckets