"""Pluggable measurement/apply abstractions for the autocal loop (roadmap Item 1a).

`MeasurementSource` and `ApplyTarget` are the two seams that keep the autocal
loop (`calibrator/autocal_loop.py`) independent of any one TV or measurement
backend. The loop orchestrator only ever calls these interfaces, never ADB or
the ZRO Bridge directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from calcore.models import Measurement, Patch

from . import adb_control
from .autocal import CorrectionResult


class MeasurementSource(ABC):
    """Triggers a measurement and returns the resulting reading."""

    @abstractmethod
    def measure(self, patch: Patch) -> Measurement:
        ...


class CallableMeasurementSource(MeasurementSource):
    """Adapts any zero-arg measurement trigger into a MeasurementSource.

    Today's ZRO Bridge (and Argyll's ``spotread``) read back whatever pattern
    is currently displayed rather than accepting patch RGB directly — see
    `docs/autocal-roadmap.md` Item 3's note on `/measure/sequence` ignoring
    RGB. This adapter is deliberately patch-agnostic so it matches that
    real-world behavior instead of pretending the trigger can set the patch.
    """

    def __init__(self, trigger: Callable[[], Measurement]) -> None:
        self._trigger = trigger

    def measure(self, patch: Patch) -> Measurement:
        return self._trigger()


@dataclass
class ApplyResult:
    ok: bool
    control: str
    step: int
    new_value: int
    message: str
    requires_user_action: bool = False


class ApplyTarget(ABC):
    """Applies a CorrectionResult to one CMS colour's control."""

    @abstractmethod
    def apply(self, correction: CorrectionResult, *, colour: str) -> ApplyResult:
        ...


class ManualApplyTarget(ApplyTarget):
    """Universal apply path: describe the change; the user makes it on the TV.

    Never touches the TV — the loop orchestrator advances only after the
    caller confirms the user re-measured (see `calibrator/autocal_loop.py`).
    """

    def apply(self, correction: CorrectionResult, *, colour: str) -> ApplyResult:
        if correction.step == 0:
            message = f"{colour} {correction.control}: no change needed ({correction.reason})"
        else:
            verb = "Raise" if correction.step > 0 else "Lower"
            message = f"{colour}: {verb} {correction.control} to {correction.new_value}"
        return ApplyResult(
            ok=True,
            control=correction.control,
            step=correction.step,
            new_value=correction.new_value,
            message=message,
            requires_user_action=correction.step != 0,
        )


class AdbApplyTarget(ApplyTarget):
    """Auto-apply path: push the correction via ADB with read-back verification.

    Falling back to manual mode on ADB failure is Item 1f's job, not this
    interface's — this class only reports success/failure honestly.
    """

    def __init__(self, device: Optional[str] = None) -> None:
        self.device = device

    def apply(self, correction: CorrectionResult, *, colour: str) -> ApplyResult:
        if colour not in adb_control.CMS_CHANNELS:
            return ApplyResult(
                ok=False,
                control=correction.control,
                step=0,
                new_value=correction.new_value,
                message=f"Unknown CMS colour {colour!r}",
            )
        if correction.step == 0:
            return ApplyResult(
                ok=True,
                control=correction.control,
                step=0,
                new_value=correction.new_value,
                message="No change needed",
            )
        try:
            set_result = adb_control.set_cms_value(
                colour, correction.control, correction.new_value, device=self.device
            )
        except ValueError as exc:
            return ApplyResult(
                ok=False,
                control=correction.control,
                step=correction.step,
                new_value=correction.new_value,
                message=str(exc),
            )
        if not set_result["ok"]:
            return ApplyResult(
                ok=False,
                control=correction.control,
                step=correction.step,
                new_value=correction.new_value,
                message=f"ADB set failed: {set_result['stderr'] or set_result['stdout']}",
            )
        readback = adb_control.get_cms_value(colour, correction.control, device=self.device)
        if not readback["ok"] or readback["value"] != correction.new_value:
            return ApplyResult(
                ok=False,
                control=correction.control,
                step=correction.step,
                new_value=correction.new_value,
                message=f"Read-back mismatch: expected {correction.new_value}, got {readback.get('value')}",
            )
        return ApplyResult(
            ok=True,
            control=correction.control,
            step=correction.step,
            new_value=correction.new_value,
            message=f"Applied {correction.control}={correction.new_value} on {colour} (verified)",
        )


class FallbackApplyTarget(ApplyTarget):
    """Tries a primary apply path; degrades to a fallback on failure.

    Item 1f's "graceful fallback to manual" — wraps `AdbApplyTarget` with a
    `ManualApplyTarget` fallback so a disconnected/misbehaving TV degrades to
    a manual instruction instead of aborting the whole autocal run. The loop
    orchestrator (`autocal_loop.py`) pauses for user confirmation whenever an
    `ApplyResult.requires_user_action` comes back `True`, so falling back here
    is all that's needed to switch that one step from automatic to manual.
    """

    def __init__(self, primary: ApplyTarget, fallback: ApplyTarget) -> None:
        self.primary = primary
        self.fallback = fallback

    def apply(self, correction: CorrectionResult, *, colour: str) -> ApplyResult:
        result = self.primary.apply(correction, colour=colour)
        if result.ok:
            return result
        fallback_result = self.fallback.apply(correction, colour=colour)
        fallback_result.message = (
            f"Auto-apply failed ({result.message}); falling back to manual — {fallback_result.message}"
        )
        return fallback_result
