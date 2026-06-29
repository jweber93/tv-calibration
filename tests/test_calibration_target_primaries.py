"""Tests for CalibrationTarget primaries normalization and validation."""

import pytest

from calcore.models import CalibrationTarget, CalMode


class TestCalibrationTargetPrimaries:
    """Test CalibrationTarget handles primaries in various formats."""

    def test_primaries_as_dict(self):
        """Dict primaries are preserved and copied."""
        primaries_dict = {
            "red": (0.64, 0.33),
            "green": (0.30, 0.60),
            "blue": (0.15, 0.06),
        }
        target = CalibrationTarget(
            mode=CalMode.SDR,
            gamut="bt709",
            primaries=primaries_dict,
        )
        assert isinstance(target.primaries, dict)
        assert target.primaries == primaries_dict
        # Ensure it's a copy, not the same object
        assert target.primaries is not primaries_dict

    def test_primaries_as_tuple_of_tuples(self):
        """Tuple-of-tuples (JSON format) is converted to dict with red/green/blue keys."""
        primaries_tuple = ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06))
        target = CalibrationTarget(
            mode=CalMode.SDR,
            gamut="bt709",
            primaries=primaries_tuple,
        )
        assert isinstance(target.primaries, dict)
        assert target.primaries["red"] == (0.64, 0.33)
        assert target.primaries["green"] == (0.30, 0.60)
        assert target.primaries["blue"] == (0.15, 0.06)

    def test_primaries_as_list_of_lists(self):
        """List-of-lists (common JSON deserialization) is converted to dict."""
        primaries_list = [[0.64, 0.33], [0.30, 0.60], [0.15, 0.06]]
        target = CalibrationTarget(
            mode=CalMode.SDR,
            gamut="bt709",
            primaries=primaries_list,
        )
        assert isinstance(target.primaries, dict)
        assert target.primaries["red"] == (0.64, 0.33)
        assert target.primaries["green"] == (0.30, 0.60)
        assert target.primaries["blue"] == (0.15, 0.06)

    def test_primaries_as_list_of_tuples(self):
        """List-of-tuples (another JSON variant) is converted to dict."""
        primaries_mixed = [(0.64, 0.33), (0.30, 0.60), (0.15, 0.06)]
        target = CalibrationTarget(
            mode=CalMode.SDR,
            gamut="bt709",
            primaries=primaries_mixed,
        )
        assert isinstance(target.primaries, dict)
        assert target.primaries["red"] == (0.64, 0.33)

    def test_primaries_wrong_sequence_length(self):
        """Sequence with wrong length raises ValueError."""
        with pytest.raises(ValueError, match="exactly 3 elements"):
            CalibrationTarget(
                mode=CalMode.SDR,
                gamut="bt709",
                primaries=((0.64, 0.33), (0.30, 0.60)),  # Only 2 elements
            )

    def test_primaries_coordinate_wrong_length(self):
        """Coordinate pair with wrong length raises ValueError."""
        with pytest.raises(ValueError, match="must be a pair"):
            CalibrationTarget(
                mode=CalMode.SDR,
                gamut="bt709",
                primaries=((0.64, 0.33), (0.30, 0.60), (0.15, 0.06, 0.10)),  # 3-tuple
            )

    def test_primaries_coordinate_not_numeric(self):
        """Non-numeric coordinate raises ValueError."""
        with pytest.raises(ValueError, match="coordinates must be numbers"):
            CalibrationTarget(
                mode=CalMode.SDR,
                gamut="bt709",
                primaries=((0.64, 0.33), ("abc", 0.60), (0.15, 0.06)),
            )

    def test_primaries_coordinate_not_sequence(self):
        """Non-sequence coordinate raises ValueError."""
        with pytest.raises(ValueError, match="must be a sequence"):
            CalibrationTarget(
                mode=CalMode.SDR,
                gamut="bt709",
                primaries=((0.64, 0.33), 0.30, (0.15, 0.06)),
            )

    def test_primaries_unsupported_type(self):
        """Unsupported type (e.g., string, int) raises TypeError."""
        with pytest.raises(TypeError, match="mapping or sequence"):
            CalibrationTarget(
                mode=CalMode.SDR,
                gamut="bt709",
                primaries="invalid",  # type: ignore
            )

    def test_primaries_autodetect_from_gamut_when_empty(self):
        """Empty primaries dict triggers detect_primaries(gamut)."""
        target = CalibrationTarget(
            mode=CalMode.SDR,
            gamut="bt709",
            primaries={},
        )
        # detect_primaries should be called and return a populated dict
        assert isinstance(target.primaries, dict)
        assert "red" in target.primaries
        assert "green" in target.primaries
        assert "blue" in target.primaries

    def test_coordinates_converted_to_float(self):
        """Integer coordinates are converted to float."""
        primaries = [[64, 33], [30, 60], [15, 6]]
        target = CalibrationTarget(
            mode=CalMode.SDR,
            gamut="bt709",
            primaries=primaries,  # type: ignore
        )
        # Verify they're stored as floats (or at least as numbers)
        for name in ("red", "green", "blue"):
            x, y = target.primaries[name]
            assert isinstance(x, float)
            assert isinstance(y, float)
