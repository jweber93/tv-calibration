"""Tests for calibrator/autocal_loop.py (Autocal roadmap Item 1c)."""
import math

import pytest

from calcore.models import CalMode, CalibrationTarget, Measurement, Patch
from calibrator.autocal_apply import ApplyResult, ApplyTarget, MeasurementSource
from calibrator.autocal_loop import AutocalLoop
from calibrator.guidance import target_nits_for_colour, target_xy_for_colour

TARGET = CalibrationTarget(
    mode=CalMode.SDR, gamma=2.2, peak_luminance_nits=120.0, gamut="bt709", eotf="BT.1886"
)
CMS_CONTROLS = ["Hue", "Saturation", "Brightness"]


def _patch_for_colour(colour: str) -> Patch:
    return Patch(label=colour, r_target=1023, g_target=0, b_target=0, meas_xyz=(0, 0, 0), kind="color")


class RecordingApplyTarget(ApplyTarget):
    def __init__(self):
        self.calls = []

    def apply(self, correction, *, colour):
        self.calls.append((colour, correction.control, correction.step))
        return ApplyResult(
            ok=True,
            control=correction.control,
            step=correction.step,
            new_value=correction.new_value,
            message="applied",
        )


class SyntheticCmsSource(MeasurementSource):
    """A fake TV: measured xyY responds linearly to the CMS controls, with a
    per-colour 'true optimal' control setting where error is exactly zero."""

    K_HUE = 0.01
    K_SAT = 0.01
    K_BRI = 3.0

    def __init__(self, state, true_optimal):
        self.state = state
        self.true_optimal = true_optimal  # {colour: {"Hue": int, "Saturation": int, "Brightness": int}}

    def measure(self, patch: Patch) -> Measurement:
        colour = patch.label
        values = self.state[colour]
        optimal = self.true_optimal[colour]
        target_xy = target_xy_for_colour(TARGET, colour)
        target_nits = target_nits_for_colour(TARGET, colour)
        wx, wy = TARGET.white_point_xy

        angle_shift = (values.get("Hue", 0) - optimal["Hue"]) * self.K_HUE
        radius_scale = 1.0 + (values.get("Saturation", 0) - optimal["Saturation"]) * self.K_SAT
        dx, dy = target_xy[0] - wx, target_xy[1] - wy
        cos_a, sin_a = math.cos(angle_shift), math.sin(angle_shift)
        rx = (dx * cos_a - dy * sin_a) * radius_scale
        ry = (dx * sin_a + dy * cos_a) * radius_scale

        y_val = target_nits + (values.get("Brightness", 0) - optimal["Brightness"]) * self.K_BRI
        return Measurement(
            x=wx + rx, y=wy + ry, Y=max(y_val, 0.01), label=colour, stimulus_rgb=(1023, 0, 0)
        )


class TestAutocalLoopConvergence:
    def test_converges_to_true_optimal_within_range(self):
        state = {"Red": dict.fromkeys(CMS_CONTROLS, 0)}
        true_optimal = {"Red": {"Hue": 4, "Saturation": -3, "Brightness": 2}}
        source = SyntheticCmsSource(state, true_optimal)
        apply_target = RecordingApplyTarget()

        loop = AutocalLoop(source, apply_target, TARGET, CMS_CONTROLS, max_iterations=12)
        result = loop.run(["Red"], _patch_for_colour, initial_values=state)

        assert not result.cancelled
        colour_result = result.colours["Red"]
        assert colour_result.converged is True
        assert colour_result.stopped_reason == "quality gate met"
        assert colour_result.delta_e is not None
        assert colour_result.delta_e < 3.0
        assert apply_target.calls  # corrections were actually applied

    def test_never_converges_reports_max_iterations(self):
        state = {"Red": dict.fromkeys(CMS_CONTROLS, 0)}
        # Unreachable true optimum: no combination of clamped controls can zero the error.
        true_optimal = {"Red": {"Hue": 400, "Saturation": -300, "Brightness": 200}}
        source = SyntheticCmsSource(state, true_optimal)
        loop = AutocalLoop(source, RecordingApplyTarget(), TARGET, CMS_CONTROLS, max_iterations=5)

        result = loop.run(["Red"], _patch_for_colour, initial_values=state)

        colour_result = result.colours["Red"]
        assert colour_result.converged is False
        assert colour_result.stopped_reason == "max_iterations reached"
        assert colour_result.iterations == 5

    def test_runs_full_six_primary_pass(self):
        colours = ["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"]
        state = {c: dict.fromkeys(CMS_CONTROLS, 0) for c in colours}
        true_optimal = {c: {"Hue": 1, "Saturation": -1, "Brightness": 1} for c in colours}
        source = SyntheticCmsSource(state, true_optimal)
        loop = AutocalLoop(source, RecordingApplyTarget(), TARGET, CMS_CONTROLS, max_iterations=10)

        result = loop.run(colours, _patch_for_colour, initial_values=state)

        assert set(result.colours) == set(colours)
        assert all(cr.converged for cr in result.colours.values())

    def test_cooperative_cancel_stops_between_colours(self):
        colours = ["Red", "Green", "Blue"]
        state = {c: dict.fromkeys(CMS_CONTROLS, 0) for c in colours}
        true_optimal = {c: {"Hue": 0, "Saturation": 0, "Brightness": 0} for c in colours}
        source = SyntheticCmsSource(state, true_optimal)
        loop = AutocalLoop(source, RecordingApplyTarget(), TARGET, CMS_CONTROLS, max_iterations=10)

        seen = []

        def on_iteration(event):
            seen.append(event.colour)

        loop.on_iteration = on_iteration
        loop.cancel()  # cancel before the loop even starts
        result = loop.run(colours, _patch_for_colour, initial_values=state)

        assert result.cancelled is True
        assert result.colours == {}

    def test_never_exceeds_control_range_even_with_extreme_error(self):
        state = {"Red": {"Hue": 9, "Saturation": 9, "Brightness": 9}}
        true_optimal = {"Red": {"Hue": -900, "Saturation": -900, "Brightness": -900}}
        source = SyntheticCmsSource(state, true_optimal)
        loop = AutocalLoop(source, RecordingApplyTarget(), TARGET, CMS_CONTROLS, max_iterations=6)

        loop.run(["Red"], _patch_for_colour, initial_values=state)

        for control in CMS_CONTROLS:
            assert -10 <= state["Red"][control] <= 10
