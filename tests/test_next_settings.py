"""End-to-end tests for POST /api/session/{sid}/next-settings (#337)."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import server as server_module
from server import app as server_app
from calcore.llm import NextSettingsPrediction


def _reset_server_globals():
    server_module._sessions.clear()
    server_module._watched_session.set(None)
    server_module._dogegen_state.proc = None
    server_module._dogegen_state.started_at = None
    server_module._dogegen_state.launch_cmd = []
    server_module._dogegen_state.last_error = None
    server_module._dogegen_config = {
        "path": "",
        "resolve_host": "",
        "window_pct": 10,
        "maxcll": 1000,
    }
    server_module._prefs = {
        "dogegen": {},
        "bridge_url": "",
        "watch_folder": "",
        "llm": {"endpoint": "", "model": ""},
        "session_defaults": {
            "signal_range": "full",
            "code_scale": "8bit",
            "pattern_generator": "dogegen",
        },
    }
    server_module._llm_queues.clear()
    server_module._zro_bridge.set("")


class TestNextSettingsAPI(unittest.TestCase):
    """Integration tests for the convergence-aware next-settings endpoint."""

    def setUp(self):
        _reset_server_globals()
        self.client = TestClient(server_app)

    def tearDown(self):
        _reset_server_globals()

    def _create_session(self):
        resp = self.client.post("/api/session", json={"tv_key": "u8g"})
        self.assertEqual(resp.status_code, 200)
        return resp.json()["id"]

    def _to_post_grayscale(self, sid):
        """Navigate the session to post_grayscale with near-perfect D65 patches."""
        self.client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        self.client.post(f"/api/session/{sid}/prepared")

        header = "Date and time\tR\tG\tB\tY\tx\ty\tmsec"
        rows = [header]
        for pct in range(0, 101, 10):
            v = int(pct / 100 * 235)
            Y = pct / 100 * 120.0 if pct > 0 else 0.01
            ts = f"01/01/2024 12:00:{pct:02d}"
            rows.append(f"{ts}\t{v}\t{v}\t{v}\t{Y:.2f}\t0.3127\t0.3290\t50")
        csv_str = "\n".join(rows)

        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("pre.csv", csv_str.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # luminance
        white_csv = (
            "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            "01/01/2024 13:00:00\t235\t235\t235\t120.00\t0.3127\t0.3290\t50"
        )
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("lum.csv", white_csv.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # white_balance
        wb_csv = (
            "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            "01/01/2024 14:00:00\t188\t188\t188\t96.00\t0.3127\t0.3290\t50\n"
            "01/01/2024 14:00:05\t71\t71\t71\t36.00\t0.3127\t0.3290\t50"
        )
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("wb.csv", wb_csv.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # gamma
        gamma_csv = (
            "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            "01/01/2024 15:00:00\t47\t47\t47\t8.50\t0.3127\t0.3290\t50\n"
            "01/01/2024 15:00:05\t94\t94\t94\t29.00\t0.3127\t0.3290\t50\n"
            "01/01/2024 15:00:10\t141\t141\t141\t62.00\t0.3127\t0.3290\t50\n"
            "01/01/2024 15:00:15\t188\t188\t188\t104.00\t0.3127\t0.3290\t50"
        )
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("gamma.csv", gamma_csv.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # color_tuner
        cms_csv = (
            "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            "01/01/2024 16:00:00\t235\t16\t16\t30.00\t0.6400\t0.3300\t50\n"
            "01/01/2024 16:00:05\t16\t235\t16\t30.00\t0.3000\t0.6000\t50\n"
            "01/01/2024 16:00:10\t16\t16\t235\t30.00\t0.1500\t0.0600\t50"
        )
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("cms.csv", cms_csv.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # post_grayscale
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("post.csv", csv_str.encode(), "text/csv")},
        )

    def _configure_llm(self, sid):
        self.client.post(
            f"/api/session/{sid}/llm/configure",
            json={"endpoint": "http://localhost:4000", "model": "test-model"},
        )

    def test_returns_404_for_unknown_session(self):
        resp = self.client.post("/api/session/nonexistent/next-settings")
        self.assertEqual(resp.status_code, 404)

    def test_returns_400_when_no_measurements(self):
        sid = self._create_session()
        resp = self.client.post(f"/api/session/{sid}/next-settings")
        self.assertEqual(resp.status_code, 400)

    def test_returns_null_when_llm_not_configured(self):
        sid = self._create_session()
        self._to_post_grayscale(sid)
        resp = self.client.post(f"/api/session/{sid}/next-settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["next_settings"])
        self.assertEqual(data["reason"], "LLM not configured")

    def test_converged_records_no_round(self):
        """A converged prediction (no deltas) records no round on the session."""
        sid = self._create_session()
        self._to_post_grayscale(sid)
        self._configure_llm(sid)

        converged = NextSettingsPrediction(
            adjustments=[],
            next_step="verify",
            confidence=0.95,
            converged=True,
            stalled=False,
            rounds_used=0,
            rounds_remaining=3,
            convergence={"avg_de": 1.2, "max_de": 2.5, "gamma_deviation": None},
            message="within tolerance",
            source="converged",
        )

        with patch("server._predict_next_settings", return_value=converged):
            resp = self.client.post(f"/api/session/{sid}/next-settings")

        self.assertEqual(resp.status_code, 200)
        pred = resp.json()["next_settings"]
        self.assertTrue(pred["converged"])
        self.assertEqual(pred["next_step"], "verify")
        self.assertEqual(pred["source"], "converged")
        # Converged path produces no deltas, so no round is recorded.
        self.assertEqual(
            server_module.store.sessions[sid].get("llm_adjustment_rounds", []), []
        )

    def test_llm_path_records_round(self):
        """An out-of-tolerance prediction with deltas records one round."""
        sid = self._create_session()
        self._to_post_grayscale(sid)
        self._configure_llm(sid)

        fake = NextSettingsPrediction(
            adjustments=[
                {
                    "menu": "White Balance",
                    "setting": "R Gain",
                    "to": -3,
                    "scope": "global",
                }
            ],
            next_step="rerun_grayscale",
            confidence=0.8,
            converged=False,
            stalled=False,
            rounds_used=0,
            rounds_remaining=3,
            convergence={"avg_de": 3.5, "max_de": 5.0, "gamma_deviation": None},
            message="out of tolerance",
            source="llm",
        )

        with patch("server._predict_next_settings", return_value=fake):
            resp = self.client.post(f"/api/session/{sid}/next-settings")

        self.assertEqual(resp.status_code, 200)
        pred = resp.json()["next_settings"]
        self.assertEqual(pred["source"], "llm")
        self.assertEqual(pred["next_step"], "rerun_grayscale")

        rounds = server_module.store.sessions[sid]["llm_adjustment_rounds"]
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["round"], 1)
        self.assertEqual(rounds[0]["residual"]["avg_de"], 3.5)
        self.assertEqual(len(rounds[0]["suggested"]), 1)

    def test_null_prediction_records_no_round(self):
        """When the predictor returns None, the endpoint surfaces a reason."""
        sid = self._create_session()
        self._to_post_grayscale(sid)
        self._configure_llm(sid)

        with patch("server._predict_next_settings", return_value=None):
            resp = self.client.post(f"/api/session/{sid}/next-settings")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["next_settings"])
        self.assertEqual(data["reason"], "LLM returned no result")
        self.assertEqual(
            server_module.store.sessions[sid].get("llm_adjustment_rounds", []), []
        )


if __name__ == "__main__":
    unittest.main()
