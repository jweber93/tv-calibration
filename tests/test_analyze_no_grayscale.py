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
        
        # Should have color data
        self.assertEqual(summary.color_rows, [
            {
                "label": "768,0,0",
                "bucket": "75",
                "target_xyz": (30.92856, 15.947926, 1.4498115),  # Approximate expected values
                "measured_xyz": (30.92856, 15.947926, 1.4498115),
                "dE2000": 0.0,
                "dE2000_chroma_only": 0.0,
            },
            {
                "label": "0,768,0",
                "bucket": "75", 
                "target_xyz": (26.8188255, 53.637651, 8.9396085),  # Approximate expected values
                "measured_xyz": (26.8188255, 53.637651, 8.9396085),
                "dE2000": 0.0,
                "dE2000_chroma_only": 0.0,
            },
            {
                "label": "0,0,768",
                "bucket": "75",
                "target_xyz": (1.4498115, 8.9396085, 26.8188255),  # Approximate expected values
                "measured_xyz": (1.4498115, 8.9396085, 26.8188255),
                "dE2000": 0.0,
                "dE2000_chroma_only": 0.0,
            }
        ])
        
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


if __name__ == "__main__":
    unittest.main()