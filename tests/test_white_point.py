"""Tests for white-point-aware target XYZ and delta-E computation (Issue #369)."""
from __future__ import annotations

import math
import unittest

from calcore.analysis import analyze
from calcore.colour import D65_XYZ, D65_xy, xyY_to_xyz
from calcore.models import AnalysisConfig, Patch
from calcore.targets import target_xyz_for_patch

# D50 white point (x=0.3457, y=0.3585) — common cinematic reference
D50_xy = (0.3457, 0.3585)


def _gray_patch(pct: int, code_max: int = 255, meas_xyz=(50.0, 50.0, 50.0)) -> Patch:
    v = round(pct / 100 * code_max)
    return Patch(
        label=f"{pct}%",
        r_target=v,
        g_target=v,
        b_target=v,
        meas_xyz=meas_xyz,
        kind="grayscale",
    )


class TestTargetXYZWhitePoint(unittest.TestCase):
    """target_xyz_for_patch must use the supplied white point, not always D65."""

    def _cfg(self) -> AnalysisConfig:
        return AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709", code_max=255)

    def test_d65_default_matches_explicit_d65(self):
        patch = _gray_patch(50)
        cfg = self._cfg()
        xyz_default = target_xyz_for_patch(patch, 255, cfg, 100.0, 0.0)
        xyz_explicit = target_xyz_for_patch(patch, 255, cfg, 100.0, 0.0, D65_xy)
        self.assertAlmostEqual(xyz_default[0], xyz_explicit[0], places=6)
        self.assertAlmostEqual(xyz_default[1], xyz_explicit[1], places=6)
        self.assertAlmostEqual(xyz_default[2], xyz_explicit[2], places=6)

    def test_non_d65_white_shifts_target_xy(self):
        """A D50 white point must produce a different target XYZ than D65."""
        patch = _gray_patch(50)
        cfg = self._cfg()
        xyz_d65 = target_xyz_for_patch(patch, 255, cfg, 100.0, 0.0, D65_xy)
        xyz_d50 = target_xyz_for_patch(patch, 255, cfg, 100.0, 0.0, D50_xy)
        # Y (luminance) must be identical — only chromaticity changes
        self.assertAlmostEqual(xyz_d65[1], xyz_d50[1], places=4)
        # X and Z must differ because xy chromaticity changed
        self.assertNotAlmostEqual(xyz_d65[0], xyz_d50[0], places=3)
        self.assertNotAlmostEqual(xyz_d65[2], xyz_d50[2], places=3)

    def test_white_patch_target_equals_white_point(self):
        """100% gray target XYZ chromaticity must match the requested white point."""
        patch = _gray_patch(100)
        cfg = self._cfg()
        for wp_xy in (D65_xy, D50_xy):
            xyz = target_xyz_for_patch(patch, 255, cfg, 100.0, 0.0, wp_xy)
            X, Y, Z = xyz
            # Derived chromaticity
            x = X / (X + Y + Z)
            y = Y / (X + Y + Z)
            self.assertAlmostEqual(x, wp_xy[0], places=4)
            self.assertAlmostEqual(y, wp_xy[1], places=4)


class TestAnalyzeWhitePoint(unittest.TestCase):
    """analyze() must produce different delta-E values for different white points."""

    def _sdr_cfg(self) -> AnalysisConfig:
        return AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709", code_max=255)

    def test_d65_vs_d50_produces_different_delta_e(self):
        """When the target white point differs, delta-E values must differ too."""
        # Measure at D65 chromaticity — perfect for D65 session, wrong for D50 session
        meas_xyz = xyY_to_xyz(D65_xy[0], D65_xy[1], 50.0)
        patches = [_gray_patch(50, meas_xyz=meas_xyz)]
        cfg = self._sdr_cfg()

        summary_d65 = analyze(patches, cfg, white_point_xy=D65_xy)
        summary_d50 = analyze(patches, cfg, white_point_xy=D50_xy)

        de_d65 = summary_d65.grayscale_rows[0]["dE2000"]
        de_d50 = summary_d50.grayscale_rows[0]["dE2000"]
        # D65 measurement vs D65 target should be smaller than vs D50 target
        self.assertLess(de_d65, de_d50)

    def test_d65_default_matches_explicit_d65(self):
        """Omitting white_point_xy must produce the same result as passing D65."""
        meas_xyz = xyY_to_xyz(D65_xy[0], D65_xy[1], 50.0)
        patches = [_gray_patch(50, meas_xyz=meas_xyz)]
        cfg = self._sdr_cfg()

        summary_implicit = analyze(patches, cfg)
        summary_explicit = analyze(patches, cfg, white_point_xy=D65_xy)

        de_implicit = summary_implicit.grayscale_rows[0]["dE2000"]
        de_explicit = summary_explicit.grayscale_rows[0]["dE2000"]
        self.assertAlmostEqual(de_implicit, de_explicit, places=6)


if __name__ == "__main__":
    unittest.main()
