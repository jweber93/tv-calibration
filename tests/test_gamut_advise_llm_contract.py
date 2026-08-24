"""#651: POST /api/session/{sid}/gamut/advise must return the typed-null
contract (200 + null + reason) when the LLM is unconfigured, matching every
other LLM-gated endpoint (get_suggested_patches, post_delta_summary,
next-settings) instead of a 400.
"""

import unittest

from fastapi.testclient import TestClient

import server as server_module
from server import app as server_app


def _reset_server_globals():
    server_module._sessions.clear()
    server_module._watched_session.set(None)
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


class TestGamutAdviseLlmContract(unittest.TestCase):
    def setUp(self):
        _reset_server_globals()
        self.client = TestClient(server_app)

    def tearDown(self):
        _reset_server_globals()

    def _create_session(self):
        resp = self.client.post("/api/session", json={"tv_key": "u8g"})
        self.assertEqual(resp.status_code, 200)
        return resp.json()["id"]

    def test_returns_typed_null_when_llm_not_configured(self):
        sid = self._create_session()
        resp = self.client.post(f"/api/session/{sid}/gamut/advise")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["advice"])
        self.assertEqual(data["reason"], "LLM not configured")

    def test_no_longer_raises_400(self):
        sid = self._create_session()
        resp = self.client.post(f"/api/session/{sid}/gamut/advise")
        self.assertNotEqual(resp.status_code, 400)
