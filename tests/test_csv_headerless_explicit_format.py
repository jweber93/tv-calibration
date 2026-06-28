"""Tests for explicit format requirement on headerless CSVs (#313)."""

import textwrap
import unittest
from io import StringIO

from calcore import parse_measurement_csv


class HeaderlessExplicitFormatTests(unittest.TestCase):
    """Tests that headerless CSVs require explicit format specification."""

    def test_headerless_csv_requires_explicit_format(self):
        """Headerless CSV without format= should raise ValueError."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            """
        )
        with self.assertRaises(ValueError) as ctx:
            parse_measurement_csv(csv_text)
        self.assertIn("format", str(ctx.exception).lower())

    def test_headerless_csv_with_xyz_format(self):
        """Headerless CSV with format='XYZ' parses correctly."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            """
        )
        patches = parse_measurement_csv(csv_text, format="XYZ")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].meas_xyz, (0.3, 0.5, 0.4))
        self.assertIsNone(patches[0].meas_yxy)

    def test_headerless_csv_with_xyY_format(self):
        """Headerless CSV with format='xyY' parses correctly."""
        csv_text = textwrap.dedent(
            """\
            1,1023,1023,1023,100.0,0.3127,0.3290
            """
        )
        patches = parse_measurement_csv(csv_text, format="xyY")
        self.assertEqual(len(patches), 1)
        self.assertIsNotNone(patches[0].meas_yxy)

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

    def test_low_luminance_xyz_not_misclassified(self):
        """Low-luminance XYZ data with explicit format stays as XYZ."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            """
        )
        patches = parse_measurement_csv(csv_text, format="XYZ")
        self.assertEqual(patches[0].meas_xyz, (0.3, 0.5, 0.4))
        # If misclassified as xyY, meas_yxy would be set
        self.assertIsNone(patches[0].meas_yxy)


if __name__ == "__main__":
    unittest.main()
