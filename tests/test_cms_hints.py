"""Tests for CMS hint generation."""

from calibrator import Measurement, SDR_TARGET
from server import _cms_hints, _target_nits_for_colour, _target_xy_for_colour


def test_blue_close_measurement_uses_normal_guidance_not_u8g_hold_message():
    target_xy = _target_xy_for_colour(SDR_TARGET, "Blue")
    target_nits = _target_nits_for_colour(SDR_TARGET, "Blue")
    measurement = Measurement(
        x=target_xy[0],
        y=target_xy[1],
        Y=target_nits,
        stimulus_rgb=(16, 16, 235),
        label="Blue 100%",
    )

    hints = _cms_hints(measurement, SDR_TARGET, "Blue")

    assert hints["hold_reason"] == ""
    assert all("U8G" not in rec for rec in hints["recommendations"])
    assert hints["recommendations"] == [
        "This color is close. Re-measure once more and move to the next color."
    ]
