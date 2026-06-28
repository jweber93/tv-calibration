"""Tests for predict_initial_settings and /predicted-settings endpoint (#335)."""

import json
import unittest
from http.client import HTTPResponse
from io import BytesIO
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import server as server_module
from calcore.llm import PredictedSettings, predict_initial_settings
from calcore.models import LLMConfig
from server import app as server_app


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


# ── Unit tests for predict_initial_settings ────────────────────────────────────


class TestPredictedSettingsDataclass(unittest.TestCase):
    """Tests for the PredictedSettings dataclass."""

    def test_to_dict_basic(self):
        settings = [
            {
                "menu": "Picture",
                "setting": "Brightness",
                "value": 50,
                "scope": "global",
                "reason": "Prior sessions converged near 50.",
            }
        ]
        obj = PredictedSettings(settings=settings, confidence=0.85, source="history")
        d = obj.to_dict()
        self.assertEqual(d["settings"], settings)
        self.assertEqual(d["confidence"], 0.85)
        self.assertEqual(d["source"], "history")

    def test_to_dict_empty_settings(self):
        obj = PredictedSettings(settings=[], confidence=0.0, source="cold_start")
        d = obj.to_dict()
        self.assertEqual(d["settings"], [])
        self.assertEqual(d["confidence"], 0.0)
        self.assertEqual(d["source"], "cold_start")


class TestPredictInitialSettings(unittest.TestCase):
    """Unit tests for predict_initial_settings function."""

    def _make_llm(self, **kwargs):
        return LLMConfig(
            endpoint=kwargs.get("endpoint", "http://localhost:4000/v1/chat/completions"),
            model=kwargs.get("model", "test-model"),
            api_key="sk-test",
        )

    def test_returns_none_when_endpoint_missing(self):
        llm = LLMConfig(endpoint="", model="test-model")
        result = predict_initial_settings(
            phase="white_balance",
            history=[],
            baseline=None,
            tv_schema=None,
            llm=llm,
        )
        self.assertIsNone(result)

    def test_returns_none_when_model_missing(self):
        llm = LLMConfig(endpoint="http://localhost:4000", model="")
        result = predict_initial_settings(
            phase="white_balance",
            history=[],
            baseline=None,
            tv_schema=None,
            llm=llm,
        )
        self.assertIsNone(result)

    def test_cold_start_no_network_call(self):
        """When history is empty and baseline is None, returns cold_start without calling network."""
        llm = self._make_llm()

        with patch("urllib.request.urlopen") as mock_urlopen:
            result = predict_initial_settings(
                phase="white_balance",
                history=[],
                baseline=None,
                tv_schema=None,
                llm=llm,
            )

        mock_urlopen.assert_not_called()
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "cold_start")
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.settings, [])

    def test_happy_path_with_history(self):
        """When history exists, calls LLM and parses response."""
        llm = self._make_llm()

        canned_body = json.dumps({
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "settings": [
                            {
                                "menu": "Picture",
                                "setting": "Brightness",
                                "value": 50,
                                "scope": "global",
                                "reason": "Prior sessions converged near 50.",
                            }
                        ],
                        "confidence": 0.85,
                    })
                }
            }]
        }).encode("utf-8")

        mock_response = MagicMock()
        mock_response.read.return_value = canned_body
        mock_response.decode = MagicMock(return_value="utf-8")

        def read_side_effect():
            return canned_body

        mock_response.read = read_side_effect

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = mock_response

            result = predict_initial_settings(
                phase="white_balance",
                history=[{"date": "2024-01-01", "wb_final": []}],
                baseline=None,
                tv_schema={"Picture": {"Brightness": {"min": 0, "max": 100}}},
                llm=llm,
            )

        mock_urlopen.assert_called_once()
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "history")
        self.assertEqual(result.confidence, 0.85)
        self.assertEqual(len(result.settings), 1)
        self.assertEqual(result.settings[0]["menu"], "Picture")

    def test_http_error_returns_none(self):
        """HTTPError from LLM is caught and returns None."""
        llm = self._make_llm()

        import urllib.error

        http_err = urllib.error.HTTPError(
            url="http://localhost:4000", code=500, msg="Internal Server Error",
            hdrs={}, fp=BytesIO(b"error"),
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            result = predict_initial_settings(
                phase="gamma",
                history=[{"date": "2024-01-01"}],
                baseline=None,
                tv_schema=None,
                llm=llm,
            )

        self.assertIsNone(result)

    def test_confidence_clamped_to_range(self):
        """Confidence values outside [0, 1] are clamped."""
        llm = self._make_llm()

        for raw_conf, expected in [(1.5, 1.0), (-0.3, 0.0), (2.0, 1.0), (-1.0, 0.0)]:
            canned_body = json.dumps({
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "settings": [],
                            "confidence": raw_conf,
                        })
                    }
                }]
            }).encode("utf-8")

            mock_response = MagicMock()
            def read_side_effect():
                return canned_body
            mock_response.read = read_side_effect

            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_urlopen.return_value.__enter__.return_value = mock_response
                result = predict_initial_settings(
                    phase="white_balance",
                    history=[{"date": "2024-01-01"}],
                    baseline=None,
                    tv_schema=None,
                    llm=llm,
                )

            self.assertEqual(result.confidence, expected)

    def test_empty_choices_returns_none(self):
        """LLM response with empty choices array returns None."""
        llm = self._make_llm()

        canned_body = json.dumps({"choices": []}).encode("utf-8")
        mock_response = MagicMock()
        def read_side_effect():
            return canned_body
        mock_response.read = read_side_effect

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = mock_response
            result = predict_initial_settings(
                phase="white_balance",
                history=[{"date": "2024-01-01"}],
                baseline=None,
                tv_schema=None,
                llm=llm,
            )

        self.assertIsNone(result)

    def test_missing_message_key_returns_none(self):
        """LLM response where choices[0] lacks 'message' key returns None."""
        llm = self._make_llm()

        canned_body = json.dumps({"choices": [{"text": "no message key"}]}).encode("utf-8")
        mock_response = MagicMock()
        def read_side_effect():
            return canned_body
        mock_response.read = read_side_effect

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = mock_response
            result = predict_initial_settings(
                phase="white_balance",
                history=[{"date": "2024-01-01"}],
                baseline=None,
                tv_schema=None,
                llm=llm,
            )

        self.assertIsNone(result)

    def test_markdown_fenced_json_parsed(self):
        """LLM response with JSON wrapped in markdown code fences is parsed."""
        llm = self._make_llm()

        inner_json = json.dumps({
            "settings": [{"menu": "Picture", "setting": "Brightness", "value": 50, "scope": "global", "reason": "test"}],
            "confidence": 0.7,
        })
        fenced = f"```json\n{inner_json}\n```"

        canned_body = json.dumps({
            "choices": [{"message": {"content": fenced}}]
        }).encode("utf-8")

        mock_response = MagicMock()
        def read_side_effect():
            return canned_body
        mock_response.read = read_side_effect

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = mock_response
            result = predict_initial_settings(
                phase="white_balance",
                history=[{"date": "2024-01-01"}],
                baseline=None,
                tv_schema=None,
                llm=llm,
            )

        self.assertIsNotNone(result)
        self.assertEqual(len(result.settings), 1)

    def test_prose_wrapped_json_parsed(self):
        """LLM response with leading/trailing prose around JSON is parsed."""
        llm = self._make_llm()

        inner_json = json.dumps({
            "settings": [],
            "confidence": 0.5,
        })
        with_prose = f"Here is the result: {inner_json} Hope this helps!"

        canned_body = json.dumps({
            "choices": [{"message": {"content": with_prose}}]
        }).encode("utf-8")

        mock_response = MagicMock()
        def read_side_effect():
            return canned_body
        mock_response.read = read_side_effect

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = mock_response
            result = predict_initial_settings(
                phase="white_balance",
                history=[{"date": "2024-01-01"}],
                baseline=None,
                tv_schema=None,
                llm=llm,
            )

        self.assertIsNotNone(result)


# ── Endpoint tests ─────────────────────────────────────────────────────────────


class TestPredictedSettingsEndpoint(unittest.TestCase):
    """Integration tests for GET /api/session/{sid}/predicted-settings."""

    def setUp(self):
        _reset_server_globals()
        self.client = TestClient(server_app)

    def tearDown(self):
        _reset_server_globals()

    def _create_session(self):
        resp = self.client.post("/api/session", json={"tv_key": "u8g"})
        self.assertEqual(resp.status_code, 200)
        return resp.json()["id"]

    def test_returns_404_for_unknown_session(self):
        resp = self.client.get("/api/session/nonexistent/predicted-settings")
        self.assertEqual(resp.status_code, 404)

    def test_returns_null_when_llm_not_configured(self):
        sid = self._create_session()
        resp = self.client.get(f"/api/session/{sid}/predicted-settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["predicted"])
        self.assertEqual(data["reason"], "LLM not configured")

    def test_accepts_phase_query_param(self):
        sid = self._create_session()
        resp = self.client.get(
            f"/api/session/{sid}/predicted-settings", params={"phase": "gamma"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_cold_start_with_llm_configured(self):
        """When LLM is configured but no history, returns cold_start."""
        sid = self._create_session()
        self.client.post(
            f"/api/session/{sid}/llm/configure",
            json={"endpoint": "http://localhost:4000", "model": "test-model"},
        )

        with patch.object(server_module, "_load_history", return_value=[]), \
             patch.object(server_module, "_load_baseline", return_value=None):
            resp = self.client.get(f"/api/session/{sid}/predicted-settings")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # With no history and no baseline, should get cold_start
        self.assertIsNotNone(data["predicted"])
        self.assertEqual(data["predicted"]["source"], "cold_start")
        self.assertEqual(data["predicted"]["confidence"], 0.0)
        self.assertEqual(data["predicted"]["settings"], [])


if __name__ == "__main__":
    unittest.main()
