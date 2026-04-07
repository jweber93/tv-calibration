import logging
from typing import List, Dict, Any

from calcore.models import Measurement, Patch
from calibrator.session import ZROImportResult, CalibrationTarget

def patches_to_session_buckets(patches: List[Patch], target: CalibrationTarget) -> dict:
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "pre_measurements": [],
        "cms_measurements": [],
        "lum_measurements": [],
    }

    for patch in patches:
        measurement = Measurement()
        measurement.X = 1.0
        measurement.Y = 1.0
        measurement.y = 1.0
        measurement.Y = 1.0
        buckets["cms_measurements"].append(measurement)

    return buckets