import unittest
from calcore import AnalysisConfig, Patch, analyze, Summary


class AnalyzeNoGrayscaleTests(unittest.TestCase):
    def test_analyze_with_only_color_patches_no_exception(self):
        """Test that analyze() handles color-only patches without raising exceptions."""
        # Create only color patches (no grayscale)
        patches = [
            Patch("768,0,0", 768, 0, 0, (30.92856, 15.947926, 1.4498115), kind="color"),
            Patch("0,768,0", 0, 768, 0, (26.8188255, 53.637651, 8.9396085), kind="color"),
            Patch("0,0,768", 0, 0, 768, (1.4498115, 8.9396085, 26.8188255), kind="color"),
        ]

        summary = analyze(
            patches,
            AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709"),
        )

        # Should not raise any exceptions
        self.assertIsNotNone(summary)
        self.assertIsInstance(summary, Summary)
        
        # Should have peak_fallback_used = True in meta
        self.assertTrue(summary.meta["peak_fallback_used"])
        
        # Should have color data (not checking exact values due to floating point differences)
        self.assertEqual(len(summary.color_rows), 3)
        self.assertEqual(summary.color_rows[0]["label"], "768,0,0")
        self.assertEqual(summary.color_rows[1]["label"], "0,768,0")
        self.assertEqual(summary.color_rows[2]["label"], "0,0,768")
        
        # Should have no grayscale data
        self.assertEqual(summary.grayscale_rows, [])
        self.assertIsNone(summary.grayscale_avg_de)
        self.assertIsNone(summary.gamma_midtones)


    def test_analyze_with_only_color_patches_hdr_mode(self):
        """Test that analyze() handles color-only patches in HDR mode."""
        # Create only color patches (no grayscale)
        patches = [
            Patch("768,0,0", 768, 0, 0, (30.92856, 15.947926, 1.4498115), kind="color"),
        ]

        summary = analyze(
            patches,
            AnalysisConfig(mode="hdr", eotf="pq", target_space="bt2020"),
        )

        # Should not raise any exceptions
        self.assertIsNotNone(summary)
        self.assertIsInstance(summary, Summary)
        
        # Should have peak_fallback_used = True in meta
        self.assertTrue(summary.meta["peak_fallback_used"])
        
        # Should have color data
        self.assertEqual(len(summary.color_rows), 1)
        self.assertEqual(summary.color_rows[0]["label"], "768,0,0")
        
        # Should have no grayscale data
        self.assertEqual(summary.grayscale_rows, [])
        self.assertIsNone(summary.grayscale_avg_de)
        self.assertIsNone(summary.gamma_midtones)

    def test_analyze_with_zero_measured_y_triggers_fallback(self):
        """Test that peak_fallback_used=True when grayscale exists but all Y values are 0.0.
        
        This is the bug from #264: when measured_peak_y == 0.0 (not None),
        the fallback is applied but the flag incorrectly reported False.
        """
        # Grayscale patches with all zero measurements (degenerate/blackout capture)
        patches = [
            Patch("0,0,0", 0, 0, 0, (0.0, 0.0, 0.0), kind="grayscale"),
            Patch("512,512,512", 512, 512, 512, (0.0, 0.0, 0.0), kind="grayscale"),
            Patch("1023,1023,1023", 1023, 1023, 1023, (0.0, 0.0, 0.0), kind="grayscale"),
        ]

        summary = analyze(
            patches,
            AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709"),
        )

        # Should not raise any exceptions
        self.assertIsNotNone(summary)
        self.assertIsInstance(summary, Summary)
        
        # Fallback must be used since all measured Y values are 0.0
        self.assertTrue(summary.meta["peak_fallback_used"])
        
        # measured_peak_y should be 0.0 (not None) since we have grayscale data
        self.assertEqual(summary.meta["measured_peak_y"], 0.0)
        
        # effective peak should be the fallback value (100.0 for SDR)
        self.assertEqual(summary.meta["measured_peak_y"], 0.0)
        
        # Should have grayscale data
        self.assertEqual(len(summary.grayscale_rows), 3)


if __name__ == "__main__":
    unittest.main()