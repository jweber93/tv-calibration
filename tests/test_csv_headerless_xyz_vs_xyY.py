"""Tests for headerless CSV XYZ vs xyY disambiguation (#146, #313)."""

import textwrap
import unittest

from calcore import parse_measurement_csv


class HeaderlessXyzVsXyYTests(unittest.TestCase):
    """Regression tests for the headerless parser misclassification bug (#146, #313)."""

    def test_headerless_requires_explicit_format(self):
        """Headerless CSV without format= should raise ValueError."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            """
        )
        with self.assertRaises(ValueError) as ctx:
            parse_measurement_csv(csv_text)
        self.assertIn("format", str(ctx.exception).lower())

    def test_xyz_with_explicit_format(self):
        """XYZ values with explicit format='XYZ' are parsed correctly."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            """
        )
        patches = parse_measurement_csv(csv_text, format="XYZ")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].meas_xyz, (0.3, 0.5, 0.4))
        self.assertIsNone(patches[0].meas_yxy)

    def test_low_luminance_xyz_not_misclassified(self):
        """Low-luminance XYZ with explicit format stays as XYZ."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            """
        )
        patches = parse_measurement_csv(csv_text, format="XYZ")
        self.assertEqual(patches[0].meas_xyz, (0.3, 0.5, 0.4))
        self.assertIsNone(patches[0].meas_yxy)

    def test_valid_xyY_with_explicit_format(self):
        """Standard xyY data with explicit format='xyY' parses correctly."""
        csv_text = textwrap.dedent(
            """\
            1,1023,1023,1023,100.0,0.3127,0.3290
            """
        )
        patches = parse_measurement_csv(csv_text, format="xyY")
        self.assertEqual(len(patches), 1)
        self.assertIsNotNone(patches[0].meas_yxy)
        self.assertEqual(patches[0].meas_yxy, (100.0, 0.3127, 0.3290))

    def test_headered_csv_does_not_require_format(self):
        """Headered CSV works without explicit format specification."""
        csv_text = textwrap.dedent(
            """\
            index,r_target,g_target,b_target,x,y,z
            1,0,0,0,0.3,0.5,0.4
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)

    def test_explicit_xyY_format_forces_xyY(self):
        """format='xyY' forces xyY interpretation regardless of values."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.5,0.6,0.4
            """
        )
        patches = parse_measurement_csv(csv_text, format="xyY")
        self.assertEqual(len(patches), 1)
        self.assertIsNotNone(patches[0].meas_yxy)

    def test_explicit_xyz_format_forces_xyz(self):
        """format='XYZ' forces XYZ interpretation regardless of values."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            """
        )
        patches = parse_measurement_csv(csv_text, format="XYZ")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].meas_xyz, (0.3, 0.5, 0.4))
        self.assertIsNone(patches[0].meas_yxy)

    def test_high_luminance_xyz_not_misclassified(self):
        """High-luminance XYZ values with explicit format."""
        csv_text = textwrap.dedent(
            """\
            1,1023,1023,1023,95.047,100.0,108.883
            """
        )
        patches = parse_measurement_csv(csv_text, format="XYZ")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].meas_xyz, (95.047, 100.0, 108.883))
        self.assertIsNone(patches[0].meas_yxy)

    def test_negative_xyz_values_preserved(self):
        """Negative XYZ values stay as-is with explicit format."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.5,-0.1,0.3
            """
        )
        patches = parse_measurement_csv(csv_text, format="XYZ")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].meas_xyz, (0.5, -0.1, 0.3))
        self.assertIsNone(patches[0].meas_yxy)


if __name__ == "__main__":
    unittest.main()
