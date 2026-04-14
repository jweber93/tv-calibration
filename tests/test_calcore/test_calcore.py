import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import cli
from calcore import (
    AnalysisConfig,
    Patch,
    SessionState,
    Summary,
    analyze,
    determine_phase,
    parse_measurement_csv,
)
from calcore.llm import _resolve_endpoint, parse_adjustment_plan, build_llm_prompt
from calcore.phase import check_cms_regression


class ParseMeasurementCsvTests(unittest.TestCase):
    def test_parses_header_csv_with_xyz_columns(self):
        csv_text = textwrap.dedent(
            """\
            r_target,g_target,b_target,x,y,z
            1023,1023,1023,95.047,100.0,108.883
            768,0,0,31.844,13.135,0.933
            """
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "measurements.csv"
            path.write_text(csv_text, encoding="utf-8")

            patches = parse_measurement_csv(str(path))

        self.assertEqual(len(patches), 2)
        self.assertEqual(patches[0].kind, "grayscale")
        self.assertEqual(patches[1].kind, "color")
        self.assertEqual(patches[1].meas_xyz, (31.844, 13.135, 0.933))

    def test_parses_raw_rows_with_yxy_columns(self):
        csv_text = textwrap.dedent(
            """\
            1,1023,1023,1023,100,0.3127,0.3290
            2,768,0,0,21.0,0.6400,0.3300
            """
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "measurements.csv"
            path.write_text(csv_text, encoding="utf-8")

            patches = parse_measurement_csv(str(path))

        self.assertEqual(len(patches), 2)
        self.assertEqual(patches[0].meas_yxy, (100.0, 0.3127, 0.3290))
        self.assertIsNotNone(patches[1].meas_xyz)


class AnalyzeTests(unittest.TestCase):
    def test_analyze_returns_expected_summary_for_near_target_data(self):
        patches = [
            Patch("0,0,0", 0, 0, 0, (0.0, 0.0, 0.0), kind="grayscale"),
            Patch(
                "512,512,512",
                512,
                512,
                512,
                (19.609, 20.633, 22.469),
                kind="grayscale",
            ),
            Patch(
                "1023,1023,1023",
                1023,
                1023,
                1023,
                (95.047, 100.0, 108.883),
                kind="grayscale",
            ),
            Patch(
                "768,0,0",
                768,
                0,
                0,
                (30.92856, 15.947926, 1.4498115),
                kind="color",
            ),
            Patch(
                "0,768,0",
                0,
                768,
                0,
                (26.8188255, 53.637651, 8.9396085),
                kind="color",
            ),
        ]

        summary = analyze(
            patches,
            AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709"),
        )

        self.assertEqual(summary.meta["patch_count"], 5)
        self.assertLess(summary.grayscale_avg_de or 999.0, 0.5)
        self.assertLess(summary.color_75_avg_de or 999.0, 1.0)
        self.assertLess(abs((summary.gamma_midtones or 0.0) - 2.2), 0.1)
        self.assertEqual(summary.grayscale_over_3, 0)

    def test_analyze_raises_for_empty_patch_list(self):
        with self.assertRaisesRegex(ValueError, "No valid patches found"):
            analyze([], AnalysisConfig())

    def test_analyze_skips_gamma_log_when_relative_luminance_is_zero(self):
        patches = [
            Patch("0,0,0", 0, 0, 0, (0.0, 0.0, 0.0), kind="grayscale"),
            Patch("512,512,512", 512, 512, 512, (0.0, 0.0, 0.0), kind="grayscale"),
            Patch(
                "1023,1023,1023",
                1023,
                1023,
                1023,
                (95.047, 100.0, 108.883),
                kind="grayscale",
            ),
        ]

        summary = analyze(
            patches,
            AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709"),
        )

        self.assertIsNone(summary.grayscale_rows[1]["gamma"])
        self.assertIsNone(summary.gamma_midtones)


class DeterminePhaseTests(unittest.TestCase):
    def make_summary(self, **overrides):
        # Default grayscale values pass the tightened threshold (avg < 0.5, max < 1.0)
        base = dict(
            grayscale_avg_de=0.4,
            grayscale_max_de=0.8,
            grayscale_over_3=0,
            gamma_midtones=2.2,
            pq_err_midtones=None,
            color_75_avg_de=2.0,
            color_75_max_de=2.5,
            color_75_chroma_avg=1.0,
            color_100_avg_de=2.0,
            color_100_max_de=2.5,
            color_100_chroma_avg=1.0,
            grayscale_rows=[],
            color_rows=[{"bucket": "75"}],
            meta={},
        )
        base.update(overrides)
        return Summary(**base)

    def test_baseline_advances_to_wb(self):
        self.assertEqual(determine_phase(self.make_summary(), "baseline"), "wb")

    def test_wb_advances_to_cms_when_gray_and_eotf_are_good(self):
        self.assertEqual(determine_phase(self.make_summary(), "wb"), "cms")

    def test_cms_advances_to_verify_when_color_is_good(self):
        self.assertEqual(determine_phase(self.make_summary(), "cms"), "verify")

    def test_wb_stays_mpwb_when_grayscale_needs_more_work(self):
        summary = self.make_summary(grayscale_avg_de=2.5, grayscale_max_de=3.5)
        self.assertEqual(determine_phase(summary, "wb"), "mpwb")

    # Tightened threshold boundary tests (#98)
    def test_wb_advances_to_cms_at_tightened_threshold(self):
        # avg=0.4 (< 0.5) and max=0.8 (< 1.0) → should unlock CMS
        summary = self.make_summary(grayscale_avg_de=0.4, grayscale_max_de=0.8)
        self.assertEqual(determine_phase(summary, "wb"), "cms")

    def test_wb_stays_mpwb_just_above_avg_threshold(self):
        # avg=0.6 (>= 0.5) → should remain mpwb even if max is fine
        summary = self.make_summary(grayscale_avg_de=0.6, grayscale_max_de=0.8)
        self.assertEqual(determine_phase(summary, "wb"), "mpwb")

    def test_wb_stays_mpwb_just_above_max_threshold(self):
        # avg passes but max=1.1 (>= 1.0) → should remain mpwb
        summary = self.make_summary(grayscale_avg_de=0.4, grayscale_max_de=1.1)
        self.assertEqual(determine_phase(summary, "wb"), "mpwb")


class CheckCmsRegressionTests(unittest.TestCase):
    def make_summary(self, grayscale_avg_de):
        return Summary(
            grayscale_avg_de=grayscale_avg_de,
            grayscale_max_de=0.5,
            grayscale_over_3=0,
            gamma_midtones=2.2,
            pq_err_midtones=None,
            color_75_avg_de=None,
            color_75_max_de=None,
            color_75_chroma_avg=None,
            color_100_avg_de=None,
            color_100_max_de=None,
            color_100_chroma_avg=None,
            grayscale_rows=[],
            color_rows=[],
            meta={},
        )

    def test_no_regression_returns_none(self):
        # baseline 0.3, current 0.4 — delta 0.1 < threshold 0.3
        summary = self.make_summary(grayscale_avg_de=0.4)
        self.assertIsNone(check_cms_regression(summary, baseline_de=0.3))

    def test_regression_above_threshold_returns_warning(self):
        # baseline 0.3, current 0.7 — delta 0.4 > threshold 0.3
        summary = self.make_summary(grayscale_avg_de=0.7)
        result = check_cms_regression(summary, baseline_de=0.3)
        self.assertIsNotNone(result)
        self.assertIn("re-run White Balance", result)

    def test_none_grayscale_de_returns_none(self):
        summary = self.make_summary(grayscale_avg_de=None)
        self.assertIsNone(check_cms_regression(summary, baseline_de=0.3))


class SweepGateTests(unittest.TestCase):
    """Tests for the partial-sweep LLM gate (#97)."""

    def make_summary(self, measured_patch_count=0, expected_patch_count=None):
        return Summary(
            grayscale_avg_de=0.4,
            grayscale_max_de=0.8,
            grayscale_over_3=0,
            gamma_midtones=2.2,
            pq_err_midtones=None,
            color_75_avg_de=None,
            color_75_max_de=None,
            color_75_chroma_avg=None,
            color_100_avg_de=None,
            color_100_max_de=None,
            color_100_chroma_avg=None,
            grayscale_rows=[],
            color_rows=[],
            meta={},
            measured_patch_count=measured_patch_count,
            expected_patch_count=expected_patch_count,
        )

    def test_partial_sweep_is_incomplete(self):
        summary = self.make_summary(measured_patch_count=3)
        self.assertFalse(summary.is_sweep_complete)

    def test_full_sweep_is_complete(self):
        summary = self.make_summary(measured_patch_count=10)
        self.assertTrue(summary.is_sweep_complete)

    def test_expected_count_gates_completion(self):
        summary = self.make_summary(measured_patch_count=8, expected_patch_count=21)
        self.assertFalse(summary.is_sweep_complete)

    def test_expected_count_unlocks_when_met(self):
        summary = self.make_summary(measured_patch_count=21, expected_patch_count=21)
        self.assertTrue(summary.is_sweep_complete)


class ParseAdjustmentPlanTests(unittest.TestCase):
    """Tests for parse_adjustment_plan (#95)."""

    VALID_JSON = json.dumps(
        {
            "adjustments": [
                {
                    "menu": "White Balance",
                    "setting": "B-Gain",
                    "from": 0,
                    "to": -3,
                    "scope": "global",
                    "reason": "Blue push at 80% lifting xy above D65 locus.",
                }
            ],
            "next_step": "rerun_grayscale",
            "confidence": 0.85,
        }
    )

    def test_parses_valid_json(self):
        plan = parse_adjustment_plan(self.VALID_JSON)
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.adjustments), 1)
        self.assertEqual(plan.adjustments[0]["setting"], "B-Gain")
        self.assertEqual(plan.next_step, "rerun_grayscale")
        self.assertAlmostEqual(plan.confidence, 0.85)

    def test_strips_markdown_fences(self):
        fenced = f"```json\n{self.VALID_JSON}\n```"
        plan = parse_adjustment_plan(fenced)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.next_step, "rerun_grayscale")

    def test_returns_none_for_invalid_json(self):
        self.assertIsNone(parse_adjustment_plan("not json at all"))

    def test_returns_none_when_adjustments_missing(self):
        bad = json.dumps({"next_step": "verify", "confidence": 1.0})
        self.assertIsNone(parse_adjustment_plan(bad))

    def test_empty_adjustments_list_is_valid(self):
        empty = json.dumps(
            {"adjustments": [], "next_step": "rerun_grayscale", "confidence": 0.0}
        )
        plan = parse_adjustment_plan(empty)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.adjustments, [])


class BuildLlmPromptTests(unittest.TestCase):
    """Tests for build_llm_prompt changes (#95, #96, #99)."""

    def make_summary(self):
        return Summary(
            grayscale_avg_de=0.4,
            grayscale_max_de=0.8,
            grayscale_over_3=0,
            gamma_midtones=2.2,
            pq_err_midtones=None,
            color_75_avg_de=None,
            color_75_max_de=None,
            color_75_chroma_avg=None,
            color_100_avg_de=None,
            color_100_max_de=None,
            color_100_chroma_avg=None,
            grayscale_rows=[],
            color_rows=[],
            meta={},
            measured_patch_count=10,
        )

    def test_prompt_requests_json_output(self):
        cfg = AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709")
        prompt = build_llm_prompt(self.make_summary(), cfg, "wb")
        self.assertIn('"adjustments"', prompt)
        self.assertIn('"next_step"', prompt)
        self.assertIn('"confidence"', prompt)

    def test_tv_settings_injected_when_provided(self):
        from calcore.models import TVSettings

        cfg = AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709")
        settings = TVSettings(two_point_wb={"R_gain": 0, "B_gain": -3})
        prompt = build_llm_prompt(self.make_summary(), cfg, "wb", tv_settings=settings)
        self.assertIn("current_tv_settings", prompt)
        self.assertIn("B_gain", prompt)

    def test_tv_schema_injected_when_provided(self):
        cfg = AnalysisConfig(mode="sdr", eotf="gamma22", target_space="bt709")
        schema = {
            "model": "Hisense U8G",
            "settings": {"gamma": {"options": [2.0, 2.2, 2.4]}},
        }
        prompt = build_llm_prompt(self.make_summary(), cfg, "wb", tv_schema=schema)
        self.assertIn("tv_settings_schema", prompt)
        self.assertIn("Hisense U8G", prompt)


class ResolveEndpointTests(unittest.TestCase):
    def test_resolve_endpoint_accepts_root_base_and_full_paths(self):
        self.assertEqual(
            _resolve_endpoint("http://localhost:4000"),
            "http://localhost:4000/v1/chat/completions",
        )
        self.assertEqual(
            _resolve_endpoint("http://localhost:4000/v1"),
            "http://localhost:4000/v1/chat/completions",
        )
        self.assertEqual(
            _resolve_endpoint("http://localhost:4000/v1/chat/completions"),
            "http://localhost:4000/v1/chat/completions",
        )


class WatchTests(unittest.TestCase):
    def test_watch_does_not_advance_mtime_when_run_fails(self):
        state = SessionState()
        csv_path = Path("/tmp/measurements.csv")
        state_path = Path("/tmp/state.json")

        with (
            mock.patch("cli.run_once", side_effect=RuntimeError("boom")),
            mock.patch("cli.save_state") as save_state_mock,
            mock.patch("cli.time.sleep", side_effect=KeyboardInterrupt),
            mock.patch("pathlib.Path.stat", return_value=mock.Mock(st_mtime=123.0)),
        ):
            with self.assertRaises(KeyboardInterrupt):
                cli.watch(csv_path, state_path, state, interval=0)

        self.assertEqual(state.last_mtime, 0.0)
        save_state_mock.assert_not_called()


class LlmResponseEdgeCasesTests(unittest.TestCase):
    """Tests for LLM response edge cases (#132)."""

    def test_parse_adjustment_plan_missing_adjustments_key(self):
        data = json.dumps({"next_step": "verify", "confidence": 1.0})
        result = parse_adjustment_plan(data)
        self.assertIsNone(result)

    def test_parse_adjustment_plan_missing_next_step(self):
        data = json.dumps({"adjustments": [], "confidence": 0.5})
        result = parse_adjustment_plan(data)
        self.assertIsNone(result)

    def test_parse_adjustment_plan_missing_confidence(self):
        data = json.dumps({"adjustments": [], "next_step": "verify"})
        result = parse_adjustment_plan(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.confidence, 0.5)

    def test_parse_adjustment_plan_empty_string(self):
        result = parse_adjustment_plan("")
        self.assertIsNone(result)

    def test_parse_adjustment_plan_whitespace_only(self):
        result = parse_adjustment_plan("   \n\t  ")
        self.assertIsNone(result)

    def test_parse_adjustment_plan_null_in_response(self):
        data = json.dumps(
            {"adjustments": [], "next_step": "verify", "confidence": None}
        )
        result = parse_adjustment_plan(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.confidence, 0.5)

    def test_parse_adjustment_plan_adjustments_not_list(self):
        data = json.dumps(
            {"adjustments": "not a list", "next_step": "verify", "confidence": 0.5}
        )
        result = parse_adjustment_plan(data)
        self.assertIsNone(result)

    def test_parse_adjustment_plan_adjustments_dict(self):
        data = json.dumps(
            {"adjustments": {"key": "value"}, "next_step": "verify", "confidence": 0.5}
        )
        result = parse_adjustment_plan(data)
        self.assertIsNone(result)

    def test_parse_adjustment_plan_adjustment_missing_fields(self):
        data = json.dumps(
            {
                "adjustments": [{"menu": "White Balance"}],
                "next_step": "verify",
                "confidence": 0.5,
            }
        )
        result = parse_adjustment_plan(data)
        self.assertIsNotNone(result)
        self.assertEqual(len(result.adjustments), 1)
        self.assertEqual(result.adjustments[0].get("menu"), "White Balance")

    def test_parse_adjustment_plan_valid_json_string(self):
        invalid = '{"adjustments": [], "next_step": "verify", "confidence": 0.5}'
        result = parse_adjustment_plan(invalid)
        self.assertIsNotNone(result)

    def test_parse_adjustment_plan_confidence_out_of_range(self):
        data = json.dumps({"adjustments": [], "next_step": "verify", "confidence": 1.5})
        result = parse_adjustment_plan(data)
        self.assertIsNotNone(result)
        self.assertEqual(result.confidence, 1.5)


if __name__ == "__main__":
    unittest.main()
