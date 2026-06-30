"""Regression coverage for the Delta E migration from CIE76 to CIEDE2000."""

import pytest

from calcore.colour import delta_e_cie76
from calibrator.utils import delta_e_ciede2000_xyY


@pytest.mark.parametrize(
    ("name", "measured", "target", "expected_de76", "expected_de00"),
    [
        (
            "identical_d65",
            (0.3127, 0.3290, 100.0),
            (0.3127, 0.3290, 100.0),
            0.0,
            0.0,
        ),
        (
            "d65_luminance_plus20",
            (0.3127, 0.3290, 120.0),
            (0.3127, 0.3290, 100.0),
            7.27,
            4.03,
        ),
        (
            "white_x_high",
            (0.3200, 0.3290, 100.0),
            (0.3127, 0.3290, 100.0),
            4.10,
            5.25,
        ),
        (
            "white_y_high",
            (0.3127, 0.3360, 100.0),
            (0.3127, 0.3290, 100.0),
            4.42,
            5.21,
        ),
        (
            "gray_50pct_overbright",
            (0.3127, 0.3290, 60.0),
            (0.3127, 0.3290, 26.0223),
            23.78,
            18.41,
        ),
        (
            "rec709_red_aligned",
            (0.6400, 0.3300, 21.3),
            (0.6400, 0.3300, 21.3),
            0.0,
            0.0,
        ),
        (
            "rec709_red_misaligned",
            (0.6200, 0.3400, 21.3),
            (0.6400, 0.3300, 21.3),
            9.01,
            1.80,
        ),
        (
            "rec709_green_misaligned",
            (0.2900, 0.5800, 71.5),
            (0.3000, 0.6000, 71.5),
            9.93,
            2.82,
        ),
        (
            "rec709_blue_misaligned",
            (0.1600, 0.0800, 7.2),
            (0.1500, 0.0600, 7.2),
            28.48,
            4.63,
        ),
        (
            "rec709_yellow_misaligned",
            (0.4100, 0.5100, 92.8),
            (0.4193, 0.5053, 92.8),
            5.18,
            1.95,
        ),
    ],
)
def test_delta_e_migration_reference_pairs(
    name,
    measured,
    target,
    expected_de76,
    expected_de00,
):
    de76 = delta_e_cie76(*measured, *target)
    de00 = delta_e_ciede2000_xyY(*measured, *target)

    assert de76 == pytest.approx(expected_de76, abs=0.03), name
    assert de00 == pytest.approx(expected_de00, abs=0.03), name


def test_ciede2000_tracks_tighter_for_colour_patch_misalignments():
    colour_pairs = [
        ((0.6200, 0.3400, 21.3), (0.6400, 0.3300, 21.3)),
        ((0.2900, 0.5800, 71.5), (0.3000, 0.6000, 71.5)),
        ((0.1600, 0.0800, 7.2), (0.1500, 0.0600, 7.2)),
        ((0.4100, 0.5100, 92.8), (0.4193, 0.5053, 92.8)),
    ]

    for measured, target in colour_pairs:
        de76 = delta_e_cie76(*measured, *target)
        de00 = delta_e_ciede2000_xyY(*measured, *target)
        assert de00 < de76
