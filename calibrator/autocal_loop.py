"""Per-primary measure -> correct -> apply -> re-measure orchestrator (roadmap Item 1c)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional

from calcore.models import CalibrationTarget, Measurement, Patch

from .autocal import CmsCorrectionController, ControllerConfig, DEFAULT_CONFIG, CorrectionResult
from .autocal_apply import ApplyResult, ApplyTarget, MeasurementSource
from .guidance import cms_hints
from .quality import QG_CMS_DE_THRESHOLD
from .session import m_to_dict

# Maps CMS control labels to the corresponding cms_hints() error key. Some TV
# profiles use "Luminance" instead of "Brightness" for the same control.
CONTROL_ERROR_KEY: Dict[str, str] = {
    "Hue": "hue",
    "Saturation": "saturation",
    "Brightness": "brightness",
    "Luminance": "brightness",
}


@dataclass
class IterationEvent:
    colour: str
    control: str
    iteration: int
    error: float
    correction: CorrectionResult
    apply_result: ApplyResult


@dataclass
class ColourResult:
    colour: str
    iterations: int
    converged: bool
    stopped_reason: str
    delta_e: Optional[float] = None
    history: List[IterationEvent] = field(default_factory=list)


@dataclass
class AutocalRunResult:
    colours: Dict[str, ColourResult]
    cancelled: bool


class AutocalLoop:
    """Drives the CMS autocal loop for a set of primaries.

    Depends only on `MeasurementSource` / `ApplyTarget`, never on ADB or the
    ZRO Bridge directly (roadmap design principle). `cancel()` is safe to call
    from another thread; the loop checks it between measurements so a running
    autocal run can be stopped cooperatively.
    """

    def __init__(
        self,
        measurement_source: MeasurementSource,
        apply_target: ApplyTarget,
        target: CalibrationTarget,
        cms_controls: List[str],
        *,
        signal_range: str = "auto",
        code_scale: str = "8bit",
        config: ControllerConfig = DEFAULT_CONFIG,
        max_iterations: int = 8,
        on_iteration: Optional[Callable[[IterationEvent], None]] = None,
    ) -> None:
        self.measurement_source = measurement_source
        self.apply_target = apply_target
        self.target = target
        self.cms_controls = [c for c in cms_controls if c in CONTROL_ERROR_KEY]
        self.signal_range = signal_range
        self.code_scale = code_scale
        self.config = config
        self.max_iterations = max_iterations
        self.on_iteration = on_iteration
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(
        self,
        colours: List[str],
        patch_for_colour: Callable[[str], Patch],
        initial_values: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> AutocalRunResult:
        results: Dict[str, ColourResult] = {}
        values = initial_values if initial_values is not None else {}
        for colour in colours:
            if self._cancel.is_set():
                break
            colour_values = values.setdefault(colour, dict.fromkeys(self.cms_controls, 0))
            results[colour] = self._run_colour(colour, patch_for_colour(colour), colour_values)
        return AutocalRunResult(colours=results, cancelled=self._cancel.is_set())

    def _run_colour(self, colour: str, patch: Patch, values: Dict[str, int]) -> ColourResult:
        controller = CmsCorrectionController(self.config)
        history: List[IterationEvent] = []
        last_de: Optional[float] = None

        for iteration in range(1, self.max_iterations + 1):
            if self._cancel.is_set():
                return ColourResult(colour, iteration - 1, False, "cancelled", last_de, history)

            measurement: Measurement = self.measurement_source.measure(patch)
            last_de = self._delta_e(measurement, colour)
            if last_de is not None and last_de < QG_CMS_DE_THRESHOLD:
                return ColourResult(colour, iteration - 1, True, "quality gate met", last_de, history)

            hints = cms_hints(measurement, self.target, colour)
            for control in self.cms_controls:
                key = CONTROL_ERROR_KEY[control]
                if key not in hints:
                    continue
                error = float(hints[key]["value"])
                current_value = values[control]
                result = controller.step(control, current_value, error)
                apply_result = self.apply_target.apply(result, colour=colour)
                if apply_result.ok:
                    values[control] = result.new_value
                event = IterationEvent(colour, control, iteration, error, result, apply_result)
                history.append(event)
                if self.on_iteration:
                    self.on_iteration(event)

        # One final measurement to report the ΔE the loop actually ended on.
        final_measurement = self.measurement_source.measure(patch)
        last_de = self._delta_e(final_measurement, colour)
        converged = last_de is not None and last_de < QG_CMS_DE_THRESHOLD
        reason = "quality gate met" if converged else "max_iterations reached"
        return ColourResult(colour, self.max_iterations, converged, reason, last_de, history)

    def _delta_e(self, measurement: Measurement, colour: str) -> Optional[float]:
        # m_to_dict() only matches a measurement to its CMS target when the
        # label equals the colour name, matching quality.py's own gate logic.
        labeled = measurement if measurement.label == colour else replace(measurement, label=colour)
        return m_to_dict(labeled, self.target, self.signal_range, self.code_scale)["delta_e"]
