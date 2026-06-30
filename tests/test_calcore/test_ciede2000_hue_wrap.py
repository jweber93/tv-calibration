"""Edge-case coverage for ciede2000()'s hue-angle wrap-around handling.

The T-factor regression fixed in issue #500 lived inside a trig expression
keyed on hp_bar (the mean hue angle). These tests exercise hp_bar values at
and around the quadrant boundaries (0/360, 90, 180, 270 degrees) -- the
0/360 boundary is the one actual wrap-around point in the algorithm (see
the dh/dhp and hp_bar averaging branches in calcore/colour.py), while the
others just give broad angular coverage of the T-factor's cosine terms.
"""

import math

import pytest

from calcore.colour import ciede2000


def _lab_at_hue(hue_deg: float, chroma: float = 20.0, L: float = 50.0):
    rad = math.radians(hue_deg)
    return (L, chroma * math.cos(rad), chroma * math.sin(rad))


@pytest.mark.parametrize(
    ("h1", "h2"),
    [
        (350.0, 10.0),  # straddles the 0/360 wrap
        (355.0, 5.0),
        (1.0, 359.0),
        (85.0, 95.0),  # around 90
        (175.0, 185.0),  # around 180
        (265.0, 275.0),  # around 270
    ],
)
def test_ciede2000_handles_hue_wrap_without_error(h1, h2):
    lab1 = _lab_at_hue(h1)
    lab2 = _lab_at_hue(h2)
    result = ciede2000(lab1, lab2)
    assert math.isfinite(result)
    assert result >= 0.0


@pytest.mark.parametrize(
    ("h1", "h2"),
    [
        (350.0, 10.0),
        (1.0, 359.0),
        (85.0, 95.0),
        (175.0, 185.0),
        (265.0, 275.0),
    ],
)
def test_ciede2000_is_symmetric_across_hue_wrap(h1, h2):
    lab1 = _lab_at_hue(h1)
    lab2 = _lab_at_hue(h2)
    assert ciede2000(lab1, lab2) == pytest.approx(ciede2000(lab2, lab1), abs=1e-9)


def test_ciede2000_is_continuous_across_zero_degree_wrap():
    # Two pairs of equally-spaced hues straddling 0/360 should not produce
    # a discontinuous jump relative to an equivalent pair that doesn't wrap.
    reference = _lab_at_hue(0.0)
    just_below = ciede2000(reference, _lab_at_hue(359.0))
    just_above = ciede2000(reference, _lab_at_hue(1.0))
    assert just_below == pytest.approx(just_above, abs=0.05)


def test_ciede2000_finite_across_full_hue_sweep():
    reference = _lab_at_hue(0.0)
    for hue in range(0, 360, 5):
        result = ciede2000(reference, _lab_at_hue(hue))
        assert math.isfinite(result)
        assert result >= 0.0
