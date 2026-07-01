"""Damped, oscillation-safe CMS correction controller (autocal roadmap Item 1b)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Mirror of adb_control.CMS_MIN / CMS_MAX. Duplicated rather than imported so the
# controller stays pure control-theory math with no dependency on the ADB module
# (roadmap design principle: "the controller never references ADB directly").
CMS_MIN = -10
CMS_MAX = 10

CMS_CONTROLS = ("Hue", "Saturation", "Brightness")


@dataclass(frozen=True)
class ControllerConfig:
    damping: float = 0.6
    # 0.5 is the binary-search contraction ratio: once an error is bracketed by a
    # sign flip, halving the effective step guarantees the bracket keeps shrinking.
    oscillation_backoff: float = 0.5
    # Never let damping collapse to 0, or the loop stalls before it converges.
    min_damping: float = 0.05
    control_min: int = CMS_MIN
    control_max: int = CMS_MAX
    # Best-effort secant gain learning; disable to run the pure damped-proportional
    # core (used to prove the proportional path is itself oscillation-safe).
    use_secant: bool = True
    # Assumed |Δerror / Δstep| per control, in cms_hints units (hue: radians,
    # saturation: xy-radius, brightness: nits). These are deliberately rough
    # a-priori guesses — the true per-TV response is nonlinear and unknown, so the
    # secant estimator overrides them as soon as one real (step, Δerror) pair exists.
    error_per_step: Dict[str, float] = field(
        default_factory=lambda: {"Hue": 0.010, "Saturation": 0.004, "Brightness": 3.0}
    )
    # Error magnitude at/below which a control is on-target. Hue/saturation mirror
    # the "is close" bands in guidance.cms_hints; brightness there is a 10% relative
    # band, so this absolute-nits default is a placeholder the orchestrator may
    # override per primary luminance.
    deadband: Dict[str, float] = field(
        default_factory=lambda: {"Hue": 0.03, "Saturation": 0.003, "Brightness": 2.0}
    )
    # Bounds how far a learned secant gain may deviate (multiplicatively) from the
    # assumed gain in one step, so a single noisy Δerror can't size an absurd step.
    # Tighter (closer to 1.0) makes gain learning slower to adapt to a TV's real
    # response but more resistant to a single noisy reading; looser risks one bad
    # measurement sizing a wild step. 8.0 was chosen empirically as the point
    # where the synthetic-model tests converge reliably without either failure mode.
    secant_gain_clamp: float = 8.0


DEFAULT_CONFIG = ControllerConfig()


@dataclass(frozen=True)
class Observation:
    error: float
    step: int


@dataclass(frozen=True)
class CorrectionResult:
    control: str
    step: int
    new_value: int
    error: float
    damping: float
    gain: float
    gain_source: str
    converged: bool
    oscillating: bool
    stalled: bool
    out_of_range: bool
    reason: str


def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _round_step(value: float) -> int:
    # Round half away from zero so a sub-unit correction still makes progress
    # rather than silently truncating to a no-op step of 0.
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _count_overshoots(history: List[Observation], error: float, deadband: float) -> int:
    # Overshoot == the error crossed zero between iterations. Counting every such
    # crossing (not just the latest) makes the effective damping a deterministic
    # function of history alone, so the controller needs no hidden mutable state.
    errors = [obs.error for obs in history] + [error]
    flips = 0
    for prev, curr in zip(errors, errors[1:]):
        if abs(prev) <= deadband or abs(curr) <= deadband:
            continue
        if _sign(prev) != _sign(curr):
            flips += 1
    return flips


def _secant_gain(
    history: List[Observation],
    error: float,
    assumed_gain: float,
    clamp: float,
) -> Optional[float]:
    if not history:
        return None
    last = history[-1]
    if last.step == 0:
        return None
    realized = (error - last.error) / last.step
    if not math.isfinite(realized) or abs(realized) <= 1e-9:
        return None
    lo = assumed_gain / clamp
    hi = assumed_gain * clamp
    magnitude = min(max(abs(realized), lo), hi)
    # Keep the realized sign: if a TV's control responds with inverted polarity the
    # secant estimate flips the step direction automatically.
    return math.copysign(magnitude, realized)


def compute_correction(
    control: str,
    current_value: int,
    error: float,
    *,
    config: ControllerConfig = DEFAULT_CONFIG,
    history: Optional[List[Observation]] = None,
) -> CorrectionResult:
    """Return a bounded, damped correction step for one CMS control.

    ``error`` is the signed measured error in cms_hints units for this control
    (positive == attribute too high, matching guidance.cms_hints). The returned
    ``step`` is an integer in control units; ``new_value`` is ``current_value +
    step`` clamped to [control_min, control_max]. ``history`` is the prior
    (error, step) observations for this control, oldest first — supply it to
    enable overshoot backoff and secant gain learning.
    """
    history = list(history or [])
    if control not in config.error_per_step or control not in config.deadband:
        raise ValueError(f"No gain/deadband configured for control {control!r}")

    assumed_gain = config.error_per_step[control]
    deadband = config.deadband[control]

    if abs(error) <= deadband:
        return CorrectionResult(
            control=control,
            step=0,
            new_value=current_value,
            error=error,
            damping=config.damping,
            gain=assumed_gain,
            gain_source="assumed",
            converged=True,
            oscillating=False,
            stalled=False,
            out_of_range=False,
            reason="within deadband",
        )

    gain = assumed_gain
    gain_source = "assumed"
    if config.use_secant:
        learned = _secant_gain(history, error, assumed_gain, config.secant_gain_clamp)
        if learned is not None:
            gain = learned
            gain_source = "secant"

    overshoots = _count_overshoots(history, error, deadband)
    effective_damping = max(
        config.min_damping, config.damping * (config.oscillation_backoff ** overshoots)
    )

    step_float = -effective_damping * error / gain
    step = _round_step(step_float)
    oscillating = overshoots > 0

    # Limit-cycle guard: if the proposed move reverses the previous move and is at
    # least as large, we would ping-pong across the target. Contract toward the
    # midpoint instead of repeating an equal-and-opposite overshoot.
    if history and history[-1].step != 0:
        last_step = history[-1].step
        if _sign(step) == -_sign(last_step) and abs(step) >= abs(last_step):
            step = _round_step(step_float * config.oscillation_backoff)
            oscillating = True

    max_up = config.control_max - current_value
    max_down = config.control_min - current_value
    clamped = min(max(step, max_down), max_up)
    out_of_range = clamped != step
    step = clamped
    new_value = current_value + step

    stalled = step == 0 and abs(error) > deadband
    if stalled and out_of_range:
        reason = f"control saturated at {new_value}; cannot correct further"
    elif stalled:
        reason = "sub-unit correction rounds to no step; cannot improve on integer control"
    elif out_of_range:
        reason = f"step clamped to control range at {new_value}"
    elif oscillating:
        reason = f"overshoot detected; step reduced (damping {effective_damping:.3f})"
    else:
        reason = f"{gain_source} damped step (damping {effective_damping:.3f})"

    return CorrectionResult(
        control=control,
        step=step,
        new_value=new_value,
        error=error,
        damping=effective_damping,
        gain=gain,
        gain_source=gain_source,
        converged=False,
        oscillating=oscillating,
        stalled=stalled,
        out_of_range=out_of_range,
        reason=reason,
    )


class CmsCorrectionController:
    """Stateful per-control wrapper that accumulates history for :func:`compute_correction`."""

    def __init__(self, config: ControllerConfig = DEFAULT_CONFIG) -> None:
        self.config = config
        self._history: Dict[str, List[Observation]] = {}

    def history(self, control: str) -> List[Observation]:
        return list(self._history.get(control, []))

    def reset(self, control: Optional[str] = None) -> None:
        if control is None:
            self._history.clear()
        else:
            self._history.pop(control, None)

    def step(self, control: str, current_value: int, error: float) -> CorrectionResult:
        history = self._history.setdefault(control, [])
        result = compute_correction(
            control, current_value, error, config=self.config, history=history
        )
        history.append(Observation(error=error, step=result.step))
        return result
