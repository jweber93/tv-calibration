"""Regression tests for calcore.gamut secondary-colour targets.

`_target_primaries()` used to approximate Cyan/Magenta/Yellow as the
arithmetic xy midpoint of two primaries instead of their true additive-mix
chromaticity. That put Magenta/Yellow 18-29 degrees of hue outside the ADB
correction gate on a display reproducing BT.709 perfectly, contradicting
the ΔE≈0 the CMS quality gate reports for the very same measurement.

See: https://github.com/jweber93/tv-calibration/issues/654
"""

import pytest

from calcore.colour import D65_xy
from calcore.gamut import _target_primaries, assess_gamut_constraints
from calcore.models import CalibrationTarget, CalMode
from calcore.spaces import BT2020_PRIMARIES, BT709_PRIMARIES, P3D65_PRIMARIES
from calibrator.guidance import target_xy_for_colour

_SECONDARY_NAMES = ("Cyan", "Magenta", "Yellow")

_SPACES = (
    ("bt709", BT709_PRIMARIES),
    ("p3d65", P3D65_PRIMARIES),
    ("bt2020", BT2020_PRIMARIES),
)


class TestSecondaryTargetsMatchGuidance:
    @pytest.mark.parametrize("gamut,primaries", _SPACES)
    def test_secondary_targets_match_guidance(self, gamut, primaries):
        # CalibrationTarget's primaries default factory always fills in
        # BT.709 regardless of `gamut` unless primaries are passed
        # explicitly, so pass them explicitly to compare like-for-like.
        target = CalibrationTarget(mode=CalMode.SDR, gamut=gamut, primaries=dict(primaries))
        gamut_targets = _target_primaries(gamut)

        for name in _SECONDARY_NAMES:
            expected = target_xy_for_colour(target, name)
            actual = gamut_targets[name]
            assert actual == pytest.approx(expected, abs=1e-4), (
                f"{gamut} {name}: gamut.py target {actual} != "
                f"guidance target {expected}"
            )


class TestPerfectDisplayHasNoUnreachablePrimaries:
    @pytest.mark.parametrize("gamut,primaries", _SPACES)
    def test_perfect_display_has_no_unreachable_primaries(self, gamut, primaries):
        target = CalibrationTarget(mode=CalMode.SDR, gamut=gamut, primaries=dict(primaries))
        rows = []
        for name in ("Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"):
            x, y = target_xy_for_colour(target, name)
            Y = 50.0
            rows.append(
                {
                    "label": name,
                    "measured_xyz": (x / y * Y, Y, (1 - x - y) / y * Y),
                }
            )

        diagnosis = assess_gamut_constraints(rows, gamut, white_xy=D65_xy)

        assert diagnosis.unreachable_primaries == []
        assert diagnosis.recommended_compromises == []
        for constraint in diagnosis.constraints:
            assert constraint.within_adb_range
