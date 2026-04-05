import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import cli
from calcore import AnalysisConfig, Patch, SessionState, Summary, analyze, determine_phase, parse_measurement_csv
from calcore.llm import _resolve_endpoint


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
        base = dict(
            grayscale_avg_de=1.0,
            grayscale_max_de=2.0,
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

        with mock.patch("cli.run_once", side_effect=RuntimeError("boom")), mock.patch(
            "cli.save_state"
        ) as save_state_mock, mock.patch(
            "cli.time.sleep", side_effect=KeyboardInterrupt
        ), mock.patch(
            "pathlib.Path.stat", return_value=mock.Mock(st_mtime=123.0)
        ):
            with self.assertRaises(KeyboardInterrupt):
                cli.watch(csv_path, state_path, state, interval=0)

        self.assertEqual(state.last_mtime, 0.0)
        save_state_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
