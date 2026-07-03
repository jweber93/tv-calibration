"""Regression test for issue #545 — limited-range analysis phantom errors.

When a session has signal_range="limited", the calcore pipeline must convert
limited-range codes (16-235) to full-range equivalents (0-255) before
normalizing against code_max.  Without this conversion, a perfect panel
produces phantom dE errors (e.g. dE 22.8 on code 82) and wrong gamma values.
"""

import math
import unittest

from calcore import AnalysisConfig, Patch, analyze
from calcore.models import _normalize_code
from calcore.targets import target_xyz_for_patch


def _xy_to_xyz(y_nits: float) -> tuple:
    """Convert Y (nits) to absolute XYZ using D65 white point."""
    # D65: x=0.3127, y=0.3290 => X = Y * x/y, Z = Y * (1-x-y)/y
    return (
        y_nits * 0.3127 / 0.3290,
        y_nits,
        y_nits * (1 - 0.3127 - 0.3290) / 0.3290,
    )


class NormalizeCodeTests(unittest.TestCase):
    """Verify _normalize_code edge cases."""

    def test_full_range_unchanged(self):
        self.assertEqual(_normalize_code(0, "full"), 0)
        self.assertEqual(_normalize_code(128, "full"), 128)
        self.assertEqual(_normalize_code(255, "full"), 255)

    def test_limited_range_boundary_low(self):
        self.assertEqual(_normalize_code(0, "limited"), 0)
        self.assertEqual(_normalize_code(16, "limited"), 0)

    def test_limited_range_boundary_high(self):
        self.assertEqual(_normalize_code(235, "limited"), 255)
        self.assertEqual(_normalize_code(255, "limited"), 255)

    def test_limited_range_midpoint(self):
        # (128 - 16) * 255 / 219 = 76.85 → 77
        self.assertEqual(_normalize_code(128, "limited"), 130)

    def test_limited_range_30_percent_gray(self):
        # (82 - 16) * 255 / 219 = 76.85 → 77
        self.assertEqual(_normalize_code(82, "limited"), 77)


class LimitedRangeAnalysisGammaTests(unittest.TestCase):
    """Verify limited-range analysis on a perfect gamma-2.2 panel.

    Uses gamma EOTF (Y = n^2.2 * peak) which is simpler than bt1886 and avoids
    black-floor degeneration issues in single-patch scenarios.
    """

    PEAK = 100.0
    GAMMA = 2.2

    def _perfect_y(self, code: int, signal_range: str) -> float:
        normed = _normalize_code(code, signal_range)
        n = normed / 255.0
        return n ** self.GAMMA * self.PEAK

    def _perfect_patch(self, code: int, signal_range: str) -> Patch:
        y = self._perfect_y(code, signal_range)
        xyz = _xy_to_xyz(y)
        return Patch(
            label=str(code),
            r_target=code, g_target=code, b_target=code,
            meas_xyz=xyz,
            meas_yxy=(y, 0.3127, 0.3290),
            kind="grayscale",
        )

    def _make_ramp(self, signal_range: str):
        """Create a 15-step grayscale ramp across the valid code range.

        Must include codes 16 (black) and 235 (white) so that measured_peak_y
        derived by analyze() equals PEAK=100, matching our pre-computed targets.
        """
        codes = [16] + list(range(36, 235, 15)) + [235]
        return [self._perfect_patch(c, signal_range) for c in codes]

    def test_perfect_limited_range_sdr_avg_de_near_zero(self):
        """Limited-range SDR on a perfect panel: avg dE < 0.5."""
        patches = self._make_ramp("limited")
        cfg = AnalysisConfig(
            mode="sdr", eotf="gamma22", target_space="bt709",
            code_max=255, signal_range="limited",
        )
        summary = analyze(patches, cfg)

        self.assertLess(summary.grayscale_avg_de if summary.grayscale_avg_de is not None else 999.0, 0.5)
        self.assertLess(summary.grayscale_max_de if summary.grayscale_max_de is not None else 999.0, 1.0)
        self.assertEqual(summary.grayscale_over_3, 0)

    def test_perfect_limited_range_sdr_gamma_near_target(self):
        """Limited-range SDR on a perfect panel: gamma ≈ 2.2."""
        patches = self._make_ramp("limited")
        cfg = AnalysisConfig(
            mode="sdr", eotf="gamma22", target_space="bt709",
            code_max=255, signal_range="limited",
        )
        summary = analyze(patches, cfg)

        gamma = summary.gamma_midtones
        self.assertIsNotNone(gamma)
        self.assertAlmostEqual(gamma, 2.2, delta=0.1)

    def test_perfect_full_range_still_works(self):
        """Full-range sessions continue to produce correct results."""
        codes = [0] + list(range(20, 255, 20)) + [255]
        patches = [self._perfect_patch(c, "full") for c in codes]
        cfg = AnalysisConfig(
            mode="sdr", eotf="gamma22", target_space="bt709",
            code_max=255, signal_range="full",
        )
        summary = analyze(patches, cfg)

        self.assertLess(summary.grayscale_avg_de if summary.grayscale_avg_de is not None else 999.0, 0.5)
        self.assertEqual(summary.grayscale_over_3, 0)

    def test_default_signal_range_is_full(self):
        """AnalysisConfig without signal_range defaults to 'full'."""
        cfg = AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709")
        self.assertEqual(cfg.signal_range, "full")

    def test_invalid_signal_range_rejected(self):
        """AnalysisConfig rejects invalid signal_range values."""
        with self.assertRaises(ValueError):
            AnalysisConfig(signal_range="invalid")


class LimitedRangeTargetXYZTests(unittest.TestCase):
    """Verify target_xyz_for_patch handles limited-range correctly."""

    def test_target_xyz_matches_full_range_equivalent(self):
        """A limited-range code should produce the same target XYZ as its
        full-range equivalent after normalization."""
        from calcore.colour import D65_xy

        limited_patch = Patch(
            "82", 82, 82, 82, (5.0, 5.0, 5.0), kind="grayscale"
        )
        full_patch = Patch(
            "77", 77, 77, 77, (5.0, 5.0, 5.0), kind="grayscale"
        )

        cfg_limited = AnalysisConfig(
            mode="sdr", eotf="gamma22", target_space="bt709",
            code_max=255, signal_range="limited",
        )
        cfg_full = AnalysisConfig(
            mode="sdr", eotf="gamma22", target_space="bt709",
            code_max=255, signal_range="full",
        )

        target_limited = target_xyz_for_patch(
            limited_patch, 255, cfg_limited, 100.0, 0.0, D65_xy
        )
        target_full = target_xyz_for_patch(
            full_patch, 255, cfg_full, 100.0, 0.0, D65_xy
        )

        for i in range(3):
            self.assertAlmostEqual(
                target_limited[i], target_full[i], places=4,
                msg=f"XYZ component {i} mismatch: "
                    f"{target_limited[i]} vs {target_full[i]}"
            )


class LimitedRangeSatBucketTests(unittest.TestCase):
    """Verify sat_bucket handles limited-range color patches correctly."""

    def test_limited_range_full_saturation_buckets_100(self):
        """Pure red at limited-range white (235,0,0) should bucket as '100'."""
        patch = Patch(
            "red", r_target=235, g_target=0, b_target=0,
            meas_xyz=(19.6, 20.0, 8.0), kind="color",
        )
        self.assertEqual(patch.sat_bucket(255, "limited"), "100")

    def test_full_range_full_saturation_buckets_100(self):
        """Pure red at full-range white (255,0,0) should still bucket as '100'."""
        patch = Patch(
            "red", r_target=255, g_target=0, b_target=0,
            meas_xyz=(19.6, 20.0, 8.0), kind="color",
        )
        self.assertEqual(patch.sat_bucket(255, "full"), "100")

    def test_limited_range_75_percent_saturation(self):
        """A color with max channel at ~75% should bucket as '75'."""
        # Limited-range 75%: (176-16)*255/219 ≈ 183
        patch = Patch(
            "75pct", r_target=176, g_target=0, b_target=0,
            meas_xyz=(19.6, 20.0, 8.0), kind="color",
        )
        self.assertEqual(patch.sat_bucket(255, "limited"), "75")


if __name__ == "__main__":
    unittest.main()
