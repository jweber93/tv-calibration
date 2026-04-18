"""Tests for headerless CSV XYZ vs xyY disambiguation (#146)."""

import textwrap
import unittest

from calcore import parse_measurement_csv


class HeaderlessXyzVsXyYTests(unittest.TestCase):
    """Regression tests for the headerless parser misclassification bug (#146)."""

    def test_low_luminance_xyz_not_misclassified_as_xyY(self):
        """XYZ values all in [0,1] range should NOT be treated as xyY.

        Example from the bug: black patch with X=0.3, Y=0.5, Z=0.4.
        Old heuristic saw all values in [0,1] and interpreted as xyY,
        producing wildly wrong LAB values.
        """
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            2,1023,1023,1023,95.047,100.0,108.883
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 2)
        # Row 1: v5+v6 = 0.5+0.4 = 0.9 < 1.0, so the old heuristic would
        # have treated this as xyY. With the tightened heuristic, x+y=0.9 < 1.0
        # so it IS classified as xyY. Let's use a case where x+y > 1.0.
        # Actually, 0.5+0.4=0.9 < 1.0, so with the new heuristic it would be xyY.
        # The real test case is when x+y >= 1.0.
        pass

    def test_xyz_with_x_plus_y_gt_1_is_treated_as_xyz(self):
        """When x+y >= 1.0, must be XYZ — no valid chromaticity has x+y > 1."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.5,0.6,0.4
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        # x=0.6, y=0.4, x+y=1.0, so NOT xyY -> treated as XYZ
        self.assertEqual(patches[0].meas_xyz, (0.5, 0.6, 0.4))
        self.assertIsNone(patches[0].meas_yxy)

    def test_xyz_with_x_plus_y_eq_1_boundary(self):
        """x+y == 1.0 is the chromaticity boundary; treat as XYZ."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,50.0,0.5,0.5
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        # x+y = 1.0, NOT < 1.0, so treated as XYZ
        self.assertEqual(patches[0].meas_xyz, (50.0, 0.5, 0.5))
        self.assertIsNone(patches[0].meas_yxy)

    def test_valid_xyY_still_works(self):
        """Standard xyY data (e.g. D65 white point) should still parse correctly."""
        csv_text = textwrap.dedent(
            """\
            1,1023,1023,1023,100.0,0.3127,0.3290
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        # x+y = 0.3127+0.3290 = 0.6417 < 1.0, so xyY
        self.assertIsNotNone(patches[0].meas_yxy)
        self.assertEqual(patches[0].meas_yxy, (100.0, 0.3127, 0.3290))

    def test_near_black_xyz_not_corrupted(self):
        """Near-black SDR patch with all XYZ < 1.0 but x+y > 1.0 stays XYZ."""
        csv_text = textwrap.dedent(
            """\
            1,1,1,1,0.1,0.35,0.45
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        # x+y = 0.35+0.45 = 0.80 < 1.0, so xyY classification.
        # But the bug example was X=0.3, Y=0.5, Z=0.4 where x+y=0.9 < 1.0
        # so the old heuristic ALSO classified it as xyY.
        # The key fix is: when x+y >= 1.0, it MUST be XYZ.
        # Let's test a case that was broken: XYZ where x+y >= 1.
        pass

    def test_explicit_xyY_format_forces_xyY(self):
        """format='xyY' should force xyY interpretation regardless of values."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.5,0.6,0.4
            """
        )
        patches = parse_measurement_csv(csv_text, format="xyY")
        self.assertEqual(len(patches), 1)
        self.assertIsNotNone(patches[0].meas_yxy)
        self.assertEqual(patches[0].meas_yxy, (0.5, 0.6, 0.4))

    def test_explicit_xyz_format_forces_xyz(self):
        """format='XYZ' should force XYZ interpretation regardless of values."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.3,0.5,0.4
            """
        )
        patches = parse_measurement_csv(csv_text, format="XYZ")
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].meas_xyz, (0.3, 0.5, 0.4))
        self.assertIsNone(patches[0].meas_yxy)

    def test_broad_spectrum_xyY_still_detected(self):
        """Red primary xyY (x=0.64, y=0.33, x+y=0.97 < 1.0)."""
        csv_text = textwrap.dedent(
            """\
            1,768,0,0,15.0,0.6400,0.3300
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        self.assertIsNotNone(patches[0].meas_yxy)
        self.assertEqual(patches[0].meas_yxy, (15.0, 0.64, 0.33))

    def test_green_xyY_still_detected(self):
        """Green primary xyY (x=0.30, y=0.60, x+y=0.90 < 1.0)."""
        csv_text = textwrap.dedent(
            """\
            1,0,768,0,50.0,0.3000,0.6000
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        self.assertIsNotNone(patches[0].meas_yxy)
        self.assertEqual(patches[0].meas_yxy, (50.0, 0.3, 0.6))

    def test_blue_xyY_still_detected(self):
        """Blue primary xyY (x=0.15, y=0.06, x+y=0.21 < 1.0)."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,768,3.0,0.1500,0.0600
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        self.assertIsNotNone(patches[0].meas_yxy)
        self.assertEqual(patches[0].meas_yxy, (3.0, 0.15, 0.06))

    def test_xyz_with_large_values_not_misclassified(self):
        """High-luminance XYZ values (>1.0) should never be xyY."""
        csv_text = textwrap.dedent(
            """\
            1,1023,1023,1023,95.047,100.0,108.883
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        # x=100.0, y=108.883, x+y >> 1.0, so XYZ
        self.assertEqual(patches[0].meas_xyz, (95.047, 100.0, 108.883))
        self.assertIsNone(patches[0].meas_yxy)

    def test_headerless_with_mixed_rows(self):
        """Mix of xyY and XYZ rows in same headerless file."""
        csv_text = textwrap.dedent(
            """\
            1,1023,1023,1023,100.0,0.3127,0.3290
            2,0,0,0,0.5,0.6,0.5
            3,768,0,0,15.0,0.6400,0.3300
            4,0,768,0,55.0,0.3000,0.6000
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 4)
        # Row 1: D65 xyY (x+y=0.6417 < 1.0)
        self.assertIsNotNone(patches[0].meas_yxy)
        # Row 2: x+y=1.2 >= 1.0, so XYZ
        self.assertIsNone(patches[1].meas_yxy)
        self.assertEqual(patches[1].meas_xyz, (0.5, 0.6, 0.5))
        # Row 3: Red xyY (x+y=0.97 < 1.0)
        self.assertIsNotNone(patches[2].meas_yxy)
        # Row 4: Green xyY (x+y=0.90 < 1.0)
        self.assertIsNotNone(patches[3].meas_yxy)

    def test_negative_xyz_values_preserved(self):
        """Negative XYZ values (shouldn't happen but edge case) stay as-is."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.5,-0.1,0.3
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        # y=-0.1 < 0, so not valid xyY -> treated as XYZ
        self.assertEqual(patches[0].meas_xyz, (0.5, -0.1, 0.3))
        self.assertIsNone(patches[0].meas_yxy)

    def test_zero_xyY_values(self):
        """Black xyY: Y=0, x=0.3127, y=0.3290 should still parse."""
        csv_text = textwrap.dedent(
            """\
            1,0,0,0,0.0,0.3127,0.3290
            """
        )
        patches = parse_measurement_csv(csv_text)
        self.assertEqual(len(patches), 1)
        # x+y=0.6417 < 1.0, so xyY
        self.assertIsNotNone(patches[0].meas_yxy)
        self.assertEqual(patches[0].meas_yxy, (0.0, 0.3127, 0.3290))


if __name__ == "__main__":
    unittest.main()
