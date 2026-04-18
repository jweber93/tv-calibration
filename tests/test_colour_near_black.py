"""Tests for near-black xyY input handling in calcore/colour.py."""

import pytest
from calcore.colour import xyY_to_xyz


def test_xyY_to_xyz_y_zero_Y_zero_returns_zeros():
    """Test that y=0, Y=0 returns (0,0,0) as before."""
    result = xyY_to_xyz(0.3127, 0.0, 0.0)
    assert result == (0.0, 0.0, 0.0)


def test_xyY_to_xyz_y_near_zero_Y_small_returns_nonzero():
    """Test that y=0.0001, Y=0.02 returns nonzero values (not (0,0,0))."""
    result = xyY_to_xyz(0.3127, 0.0001, 0.02)
    # Should not be (0,0,0) - this is the fix for the bug
    assert result != (0.0, 0.0, 0.0)
    # Should be close to D65 white point projected onto the Y=0.02 level
    X, Y, Z = result
    assert Y == 0.02  # Y should be preserved
    # X and Z should be non-zero (from projection)
    assert X > 0.0
    assert Z > 0.0


def test_xyY_to_xyz_normal_case_still_works():
    """Test that normal xyY inputs still work as expected."""
    result = xyY_to_xyz(0.3, 0.3, 100.0)
    # Standard calculation: X = (x * Y) / y, Z = ((1 - x - y) * Y) / y
    expected_X = (0.3 * 100.0) / 0.3
    expected_Z = ((1 - 0.3 - 0.3) * 100.0) / 0.3
    X, Y, Z = result
    assert X == pytest.approx(expected_X, rel=1e-10)
    assert Y == 100.0
    assert Z == pytest.approx(expected_Z, rel=1e-10)


def test_xyY_to_xyz_y_below_threshold():
    """Test that y values below _y_MIN_VALID threshold are handled properly."""
    # Using a very small y value
    result = xyY_to_xyz(0.3127, 1e-7, 50.0)
    X, Y, Z = result
    assert Y == 50.0  # Y should be preserved
    assert X > 0.0    # X should be non-zero (from projection)
    assert Z > 0.0    # Z should be non-zero (from projection)


def test_xyY_to_xyz_Y_below_threshold():
    """Test that Y values below _Y_MIN_VALID threshold are handled properly."""
    # Using a very small Y value
    result = xyY_to_xyz(0.3127, 0.3290, 1e-5)
    X, Y, Z = result
    assert Y == 1e-5  # Y should be preserved (but clamped to 0 if < _Y_MIN_VALID)
    assert X > 0.0    # X should be non-zero (from projection)
    assert Z > 0.0    # Z should be non-zero (from projection)


def test_xyY_to_xyz_y_zero_Y_positive():
    """Test that y=0, Y>0 returns projected values, not (0,0,0)."""
    result = xyY_to_xyz(0.3127, 0.0, 10.0)
    X, Y, Z = result
    assert Y == 10.0  # Y should be preserved
    # Should not be (0,0,0) - this is the fix for the bug
    assert result != (0.0, 0.0, 0.0)
    # Should be projected onto D65
    assert X > 0.0
    assert Z > 0.0