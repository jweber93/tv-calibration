"""Tests for calcore/patch_planner.py — predictive patch density planning."""

import unittest
from calcore.patch_planner import (
    SuggestedPatch,
    PatchOptimization,
    plan_patches,
    _extract_residuals,
    MAX_PATCH_BUDGET,
)


class SuggestedPatchTests(unittest.TestCase):
    """Tests for the SuggestedPatch dataclass."""

    def test_to_dict_roundtrip(self):
        patch = SuggestedPatch(
            nits=120.0,
            r=235,
            g=16,
            b=16,
            priority="high",
            label="Red 100%",
            rationale="High delta-E at red primary",
        )
        d = patch.to_dict()
        self.assertEqual(d["nits"], 120.0)
        self.assertEqual(d["r"], 235)
        self.assertEqual(d["g"], 16)
        self.assertEqual(d["b"], 16)
        self.assertEqual(d["priority"], "high")
        self.assertEqual(d["label"], "Red 100%")
        self.assertEqual(d["rationale"], "High delta-E at red primary")

    def test_from_dict(self):
        d = {
            "nits": 60.0,
            "r": 128,
            "g": 128,
            "b": 128,
            "priority": "medium",
            "label": "Gray 50%",
            "rationale": "Mid-tone gamma correction",
        }
        patch = SuggestedPatch.from_dict(d)
        self.assertAlmostEqual(patch.nits, 60.0)
        self.assertEqual(patch.r, 128)
        self.assertEqual(patch.g, 128)
        self.assertEqual(patch.b, 128)
        self.assertEqual(patch.priority, "medium")
        self.assertEqual(patch.label, "Gray 50%")
        self.assertEqual(patch.rationale, "Mid-tone gamma correction")

    def test_from_dict_defaults(self):
        d = {"nits": 0, "r": 0, "g": 0, "b": 0, "priority": "low"}
        patch = SuggestedPatch.from_dict(d)
        self.assertEqual(patch.label, "")
        self.assertEqual(patch.rationale, "")

    def test_from_dict_coerces_types(self):
        d = {"nits": "120.5", "r": "235", "g": "16", "b": "16", "priority": "high"}
        patch = SuggestedPatch.from_dict(d)
        self.assertIsInstance(patch.nits, float)
        self.assertIsInstance(patch.r, int)
        self.assertIsInstance(patch.g, int)
        self.assertIsInstance(patch.b, int)

    def test_from_dict_clamps_above_max(self):
        d = {"nits": 100, "r": 300, "g": 256, "b": 400, "priority": "high"}
        patch = SuggestedPatch.from_dict(d)
        self.assertEqual(patch.r, 255)
        self.assertEqual(patch.g, 255)
        self.assertEqual(patch.b, 255)

    def test_from_dict_clamps_negative(self):
        d = {"nits": 50, "r": -10, "g": -1, "b": 0, "priority": "medium"}
        patch = SuggestedPatch.from_dict(d)
        self.assertEqual(patch.r, 0)
        self.assertEqual(patch.g, 0)
        self.assertEqual(patch.b, 0)

    def test_from_dict_clamps_boundary(self):
        d = {"nits": 80, "r": 255, "g": 255, "b": 255, "priority": "low"}
        patch = SuggestedPatch.from_dict(d)
        self.assertEqual(patch.r, 255)
        self.assertEqual(patch.g, 255)
        self.assertEqual(patch.b, 255)

        d2 = {"nits": 80, "r": 256, "g": 0, "b": -1, "priority": "low"}
        patch2 = SuggestedPatch.from_dict(d2)
        self.assertEqual(patch2.r, 255)
        self.assertEqual(patch2.g, 0)
        self.assertEqual(patch2.b, 0)

    def test_from_dict_within_range_unchanged(self):
        d = {"nits": 120, "r": 100, "g": 150, "b": 200, "priority": "high"}
        patch = SuggestedPatch.from_dict(d)
        self.assertEqual(patch.r, 100)
        self.assertEqual(patch.g, 150)
        self.assertEqual(patch.b, 200)

    def test_from_dict_10bit_code_max(self):
        d = {"nits": 100, "r": 512, "g": 768, "b": 1023, "priority": "high"}
        patch = SuggestedPatch.from_dict(d, code_max=1023)
        self.assertEqual(patch.r, 512)
        self.assertEqual(patch.g, 768)
        self.assertEqual(patch.b, 1023)

    def test_from_dict_10bit_clamps_above_1023(self):
        d = {"nits": 100, "r": 1100, "g": 2000, "b": 1024, "priority": "high"}
        patch = SuggestedPatch.from_dict(d, code_max=1023)
        self.assertEqual(patch.r, 1023)
        self.assertEqual(patch.g, 1023)
        self.assertEqual(patch.b, 1023)

    def test_from_dict_string_values_coerced(self):
        d = {"nits": 80, "r": "200", "g": "100", "b": "50", "priority": "medium"}
        patch = SuggestedPatch.from_dict(d)
        self.assertEqual(patch.r, 200)
        self.assertEqual(patch.g, 100)
        self.assertEqual(patch.b, 50)

    def test_from_dict_string_values_clamped(self):
        d = {"nits": 80, "r": "300", "g": "-10", "b": "50", "priority": "medium"}
        patch = SuggestedPatch.from_dict(d)
        self.assertEqual(patch.r, 255)
        self.assertEqual(patch.g, 0)
        self.assertEqual(patch.b, 50)


class PatchOptimizationTests(unittest.TestCase):
    """Tests for the PatchOptimization dataclass."""

    def test_to_dict(self):
        patches = [
            SuggestedPatch(nits=120, r=235, g=16, b=16, priority="high", label="Red"),
            SuggestedPatch(nits=60, r=16, g=235, b=16, priority="medium", label="Green"),
        ]
        opt = PatchOptimization(
            patches=patches,
            rationale="Denser sampling at color primaries",
            confidence=0.85,
            auto_apply=True,
        )
        d = opt.to_dict()
        self.assertEqual(d["patch_count"], 2)
        self.assertEqual(d["rationale"], "Denser sampling at color primaries")
        self.assertEqual(d["confidence"], 0.85)
        self.assertTrue(d["auto_apply"])
        self.assertEqual(len(d["patches"]), 2)

    def test_auto_apply_logic(self):
        # auto_apply is set explicitly (not computed from confidence)
        # The LLM code sets auto_apply=confidence >= 0.7 when constructing
        opt = PatchOptimization(confidence=0.75, auto_apply=True)
        self.assertTrue(opt.auto_apply)

        opt = PatchOptimization(confidence=0.69, auto_apply=False)
        self.assertFalse(opt.auto_apply)

        opt = PatchOptimization(confidence=0.7, auto_apply=True)
        self.assertTrue(opt.auto_apply)

        opt = PatchOptimization(confidence=0.5)  # default auto_apply=False
        self.assertFalse(opt.auto_apply)

    def test_default_values(self):
        opt = PatchOptimization()
        self.assertEqual(opt.patches, [])
        self.assertEqual(opt.rationale, "")
        self.assertEqual(opt.confidence, 0.5)
        self.assertFalse(opt.auto_apply)


class ExtractResidualsTests(unittest.TestCase):
    """Tests for the _extract_residuals helper."""

    def test_sorts_by_de_descending(self):
        grayscale_rows = [
            {"label": "10% Gray", "dE2000": 5.0, "gamma": 2.1, "pq_error_pct": 10.0},
            {"label": "50% Gray", "dE2000": 1.0, "gamma": 2.2, "pq_error_pct": 2.0},
            {"label": "90% Gray", "dE2000": 3.0, "gamma": 2.3, "pq_error_pct": 5.0},
        ]
        result = _extract_residuals(grayscale_rows, [])
        labels = [r["label"] for r in result["grayscale_by_error"]]
        self.assertEqual(labels, ["10% Gray", "90% Gray", "50% Gray"])

    def test_rounds_values(self):
        grayscale_rows = [
            {"label": "Test", "dE2000": 3.4567, "gamma": 2.1234, "pq_error_pct": 5.678},
        ]
        result = _extract_residuals(grayscale_rows, [])
        row = result["grayscale_by_error"][0]
        self.assertEqual(row["dE2000"], 3.46)
        self.assertEqual(row["gamma"], 2.123)
        self.assertEqual(row["pq_error_pct"], 5.7)

    def test_handles_missing_fields(self):
        grayscale_rows = [
            {"label": "Test"},
        ]
        result = _extract_residuals(grayscale_rows, [])
        row = result["grayscale_by_error"][0]
        self.assertEqual(row["label"], "Test")
        self.assertNotIn("dE2000", row)
        self.assertNotIn("gamma", row)

    def test_color_rows_limited_to_20(self):
        color_rows = [
            {"label": f"Color {i}", "dE2000": float(i), "dE2000_chroma_only": float(i) * 0.5}
            for i in range(25)
        ]
        result = _extract_residuals([], color_rows)
        self.assertEqual(len(result["color_by_error"]), 20)

    def test_color_rows_sorted_by_de(self):
        color_rows = [
            {"label": "Low", "dE2000": 1.0},
            {"label": "High", "dE2000": 10.0},
            {"label": "Med", "dE2000": 5.0},
        ]
        result = _extract_residuals([], color_rows)
        labels = [r["label"] for r in result["color_by_error"]]
        self.assertEqual(labels, ["High", "Med", "Low"])

    def test_chroma_only_field_mapped(self):
        grayscale_rows = [
            {"label": "Test", "dE2000_chroma_only": 2.5},
        ]
        result = _extract_residuals(grayscale_rows, [])
        row = result["grayscale_by_error"][0]
        self.assertEqual(row["dE2000_chroma"], 2.5)


class PlanPatchesTests(unittest.TestCase):
    """Tests for the plan_patches public function."""

    def test_returns_residuals_and_budget(self):
        grayscale_rows = [
            {"label": "10% Gray", "dE2000": 5.0},
        ]
        color_rows = [
            {"label": "Red", "dE2000": 3.0},
        ]
        result = plan_patches(grayscale_rows, color_rows, budget=20)
        self.assertIn("residuals", result)
        self.assertIn("patch_budget", result)
        self.assertEqual(result["patch_budget"], 20)

    def test_default_budget(self):
        result = plan_patches([], [])
        self.assertEqual(result["patch_budget"], MAX_PATCH_BUDGET)

    def test_residuals_contain_sorted_data(self):
        grayscale_rows = [
            {"label": f"Gray {i}", "dE2000": 10 - i}
            for i in range(5)
        ]
        color_rows = [
            {"label": f"Color {i}", "dE2000": 8 - i}
            for i in range(5)
        ]
        result = plan_patches(grayscale_rows, color_rows)
        residuals = result["residuals"]
        gray_labels = [r["label"] for r in residuals["grayscale_by_error"]]
        self.assertEqual(gray_labels, ["Gray 0", "Gray 1", "Gray 2", "Gray 3", "Gray 4"])


if __name__ == "__main__":
    unittest.main()
