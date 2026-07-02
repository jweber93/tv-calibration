"""Tests for the damped CMS correction controller (autocal Item 1b)."""

from dataclasses import replace

from calibrator.autocal import (
    CMS_MAX,
    CMS_MIN,
    DEFAULT_CONFIG,
    CmsCorrectionController,
    ControllerConfig,
    Observation,
    compute_correction,
)


class SyntheticControl:
    """Deterministic display response: measured error as a function of one control.

    ``error = k * (value - optimal)`` with an optional asymmetric slope so the
    fixed-gain path cannot be exact and the secant estimator has to adapt. Positive
    error means "attribute too high", matching guidance.cms_hints sign convention.
    """

    def __init__(self, k: float, optimal: int, asymmetry: float = 0.0) -> None:
        self.k = k
        self.optimal = optimal
        self.asymmetry = asymmetry

    def error(self, value: int) -> float:
        diff = value - self.optimal
        slope = self.k * (1.0 + self.asymmetry * (1 if diff >= 0 else -1))
        return slope * diff


def run_loop(controller, model, control, start, cap=40):
    value = start
    errors = [model.error(value)]
    values = [value]
    converged = False
    for _ in range(cap):
        result = controller.step(control, value, model.error(value))
        assert CMS_MIN <= result.new_value <= CMS_MAX
        value = result.new_value
        values.append(value)
        errors.append(model.error(value))
        if result.converged:
            converged = True
            break
    return converged, errors, values


def _sign_flips(errors, deadband):
    flips = 0
    for prev, curr in zip(errors, errors[1:]):
        if abs(prev) <= deadband or abs(curr) <= deadband:
            continue
        if (prev > 0) != (curr > 0):
            flips += 1
    return flips


def test_converges_on_synthetic_model_for_each_control():
    models = {
        "Hue": SyntheticControl(k=0.012, optimal=2, asymmetry=0.2),
        "Saturation": SyntheticControl(k=0.006, optimal=-3, asymmetry=0.15),
        "Brightness": SyntheticControl(k=4.0, optimal=5, asymmetry=0.25),
    }
    for control, model in models.items():
        controller = CmsCorrectionController()
        converged, errors, _ = run_loop(controller, model, control, start=-8)
        deadband = DEFAULT_CONFIG.deadband[control]
        assert converged, f"{control} did not converge: {errors}"
        assert abs(errors[-1]) <= deadband
        # A well-behaved damped loop settles quickly and does not ping-pong forever.
        assert _sign_flips(errors, deadband) <= 2


def test_secant_learns_gain_where_conservative_fixed_assumption_stalls():
    # Assumed gain is ~8x too large (conservative), so the fixed-gain controller
    # takes timid single-click steps whose correction rounds to zero before reaching
    # target: it stalls short. The secant controller learns the true slope from its
    # first observation and converges.
    model = SyntheticControl(k=0.006, optimal=1)
    cfg = replace(DEFAULT_CONFIG, error_per_step={**DEFAULT_CONFIG.error_per_step, "Saturation": 0.05})
    deadband = cfg.deadband["Saturation"]

    secant = CmsCorrectionController(cfg)
    conv_s, errs_s, _ = run_loop(secant, model, "Saturation", start=-9)

    fixed = CmsCorrectionController(replace(cfg, use_secant=False))
    conv_f, errs_f, _ = run_loop(fixed, model, "Saturation", start=-9)

    assert conv_s and abs(errs_s[-1]) <= deadband
    assert not conv_f and abs(errs_f[-1]) > deadband


def test_damped_proportional_core_is_oscillation_safe_without_secant():
    # Assumed gain far below the true slope makes the raw proportional step overshoot
    # (>2x), which without backoff diverges. The overshoot backoff must rescue it.
    model = SyntheticControl(k=0.02, optimal=0)
    cfg = replace(
        DEFAULT_CONFIG,
        use_secant=False,
        error_per_step={**DEFAULT_CONFIG.error_per_step, "Saturation": 0.0025},
    )
    controller = CmsCorrectionController(cfg)
    converged, errors, _ = run_loop(controller, model, "Saturation", start=-8)
    assert converged
    assert abs(errors[-1]) <= cfg.deadband["Saturation"]


def test_without_backoff_the_same_setup_oscillates_and_does_not_converge():
    # Contrast case: disabling the backoff (ratio 1.0) on the overshooting setup
    # leaves a sustained limit cycle — proving the backoff is what fixes it.
    model = SyntheticControl(k=0.02, optimal=0)
    cfg = replace(
        DEFAULT_CONFIG,
        use_secant=False,
        oscillation_backoff=1.0,
        min_damping=0.6,
        error_per_step={**DEFAULT_CONFIG.error_per_step, "Saturation": 0.0025},
    )
    controller = CmsCorrectionController(cfg)
    converged, errors, _ = run_loop(controller, model, "Saturation", start=-8)
    assert not converged
    assert _sign_flips(errors, cfg.deadband["Saturation"]) >= 5


def test_never_exceeds_control_range_on_huge_error():
    # An error that naively demands a +100 step near the top of the range must clamp.
    result = compute_correction("Brightness", current_value=8, error=-500.0)
    assert result.new_value <= CMS_MAX
    assert result.out_of_range

    result = compute_correction("Brightness", current_value=-9, error=200.0)
    assert result.new_value >= CMS_MIN


def test_saturated_control_reports_stall_instead_of_illegal_step():
    # Already at the floor but error still demands going lower: no legal step exists.
    result = compute_correction("Saturation", current_value=CMS_MIN, error=0.08)
    assert result.step == 0
    assert result.new_value == CMS_MIN
    assert result.stalled
    assert result.out_of_range


def test_oscillation_path_reduces_step_versus_naive_repeat():
    # First move (no history) for a large positive error.
    naive_pos = compute_correction("Saturation", current_value=0, error=0.05)
    assert naive_pos.step < 0
    assert not naive_pos.oscillating

    # Now the error has flipped sign (overshoot). A naive controller would apply an
    # equal-and-opposite step; ours must detect the overshoot and shrink it.
    history = [Observation(error=0.05, step=naive_pos.step)]
    overshoot = compute_correction(
        "Saturation", current_value=naive_pos.new_value, error=-0.045, history=history
    )
    naive_flip = compute_correction("Saturation", current_value=naive_pos.new_value, error=-0.045)

    assert overshoot.oscillating
    assert overshoot.step > 0
    assert abs(overshoot.step) < abs(naive_flip.step)


def test_within_deadband_is_converged_with_zero_step():
    result = compute_correction("Hue", current_value=3, error=0.01)
    assert result.converged
    assert result.step == 0
    assert result.new_value == 3


def test_default_damping_is_in_the_specified_range():
    assert 0.5 <= ControllerConfig().damping <= 0.7


def test_inverted_polarity_control_is_corrected_by_secant_sign():
    # A TV whose control responds with inverted polarity: increasing the control
    # lowers the attribute (negative realised gain). The nominal assumption would
    # push the wrong way, but after one observation the secant flips direction.
    model = SyntheticControl(k=-0.01, optimal=2)
    controller = CmsCorrectionController()
    converged, errors, _ = run_loop(controller, model, "Saturation", start=-6)
    assert converged
    assert abs(errors[-1]) <= DEFAULT_CONFIG.deadband["Saturation"]
