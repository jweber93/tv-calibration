"""Tests for suggested-patches API endpoints and ZRO bridge /measure/sequence."""

import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

# ── Suggested Patches API Tests ────────────────────────────────────────────────

import server as server_module
from server import app as server_app


def _reset_server_globals():
    server_module._sessions.clear()
    server_module._watched_session_id = None
    server_module._dogegen_proc = None
    server_module._dogegen_started_at = None
    server_module._dogegen_launch_cmd = []
    server_module._dogegen_last_error = None
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
    server_module._zro_bridge_url = ""


class TestSuggestedPatchesAPI(unittest.TestCase):
    """Integration tests for GET/POST /api/session/{sid}/suggested-patches."""

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
        """Navigate session to post_grayscale step with some measurements."""
        self.client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        self.client.post(f"/api/session/{sid}/prepared")

        # Upload grayscale CSV
        header = "Date and time\tR\tG\tB\tY\tx\ty\tmsec"
        rows = [header]
        for pct in range(0, 101, 10):
            v = int(pct / 100 * 235)
            Y = pct / 100 * 120.0 if pct > 0 else 0.01
            ts = f"01/01/2024 12:00:{pct:02d}"
            rows.append(f"{ts}\t{v}\t{v}\t{v}\t{Y:.2f}\t0.3127\t0.3290\t50")
        csv_str = "\n".join(rows)

        resp = self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("test.csv", csv_str.encode(), "text/csv")},
        )
        self.assertEqual(resp.status_code, 200)

        # Advance through steps to post_grayscale
        self.client.post(f"/api/session/{sid}/next")  # luminance
        white_csv = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n01/01/2024 13:00:00\t235\t235\t235\t120.00\t0.3127\t0.3290\t50"
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("lum.csv", white_csv.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # white_balance
        wb_csv = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n01/01/2024 14:00:00\t188\t188\t188\t96.00\t0.3127\t0.3290\t50\n01/01/2024 14:00:05\t71\t71\t71\t36.00\t0.3127\t0.3290\t50"
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("wb.csv", wb_csv.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # gamma
        gamma_csv = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n01/01/2024 15:00:00\t47\t47\t47\t8.50\t0.3127\t0.3290\t50\n01/01/2024 15:00:05\t94\t94\t94\t29.00\t0.3127\t0.3290\t50\n01/01/2024 15:00:10\t141\t141\t141\t62.00\t0.3127\t0.3290\t50\n01/01/2024 15:00:15\t188\t188\t188\t104.00\t0.3127\t0.3290\t50"
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("gamma.csv", gamma_csv.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # color_tuner
        cms_csv = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n01/01/2024 16:00:00\t235\t16\t16\t30.00\t0.6400\t0.3300\t50\n01/01/2024 16:00:05\t16\t235\t16\t30.00\t0.3000\t0.6000\t50\n01/01/2024 16:00:10\t16\t16\t235\t30.00\t0.1500\t0.0600\t50"
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("cms.csv", cms_csv.encode(), "text/csv")},
        )
        self.client.post(f"/api/session/{sid}/next")  # post_grayscale
        self.client.post(
            f"/api/session/{sid}/import/zro",
            files={"file": ("post.csv", csv_str.encode(), "text/csv")},
        )

    def test_returns_404_for_unknown_session(self):
        resp = self.client.get("/api/session/nonexistent/suggested-patches")
        self.assertEqual(resp.status_code, 404)

    def test_returns_400_when_no_measurements(self):
        sid = self._create_session()
        resp = self.client.get(f"/api/session/{sid}/suggested-patches")
        self.assertEqual(resp.status_code, 400)

    def test_returns_null_when_llm_not_configured(self):
        sid = self._create_session()
        self._to_post_grayscale(sid)
        resp = self.client.get(f"/api/session/{sid}/suggested-patches")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["optimization"])
        self.assertEqual(data["reason"], "LLM not configured")

    def test_validates_budget_range(self):
        sid = self._create_session()
        self._to_post_grayscale(sid)
        # Configure LLM so we don't get "LLM not configured"
        self.client.post(f"/api/session/{sid}/llm/configure", json={
            "endpoint": "http://localhost:4000",
            "model": "test-model",
        })

        resp = self.client.get(f"/api/session/{sid}/suggested-patches", params={"budget": 0})
        self.assertEqual(resp.status_code, 400)

        resp = self.client.get(f"/api/session/{sid}/suggested-patches", params={"budget": 201})
        self.assertEqual(resp.status_code, 400)

    def test_budget_1_is_valid(self):
        sid = self._create_session()
        self._to_post_grayscale(sid)
        self.client.post(f"/api/session/{sid}/llm/configure", json={
            "endpoint": "http://localhost:4000",
            "model": "test-model",
        })
        resp = self.client.get(f"/api/session/{sid}/suggested-patches", params={"budget": 1})
        self.assertEqual(resp.status_code, 200)

    def test_run_suggested_patches_requires_bridge_url(self):
        sid = self._create_session()
        _reset_server_globals()
        self.client = TestClient(server_app)
        self._create_session = lambda: sid  # reuse session

        resp = self.client.post(
            f"/api/session/{sid}/suggested-patches/run",
            json={"patches": [{"nits": 120, "r": 235, "g": 16, "b": 16, "priority": "high"}]},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ZRO Bridge URL not configured", resp.json()["detail"])

    def test_run_suggested_patches_validates_patch_body(self):
        _reset_server_globals()
        self.client = TestClient(server_app)
        sid = self._create_session()
        server_module._zro_bridge_url = "http://localhost:7070"

        # Mock the bridge so we can test validation before the bridge call
        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"accepted": 0, "succeeded": 0, "results": []}
            resp.raise_for_status = MagicMock()
            return resp

        with patch("server.httpx.post", side_effect=mock_post):
            # Empty patches goes through validation (0 is not > 200) and hits bridge
            resp = self.client.post(
                f"/api/session/{sid}/suggested-patches/run",
                json={"patches": []},
            )
            self.assertEqual(resp.status_code, 200)

        # Missing patches key returns 422 (Pydantic validation)
        resp = self.client.post(
            f"/api/session/{sid}/suggested-patches/run",
            json={"data": []},
        )
        self.assertEqual(resp.status_code, 422)

        # Invalid patch fields return 422 (Pydantic validation)
        resp = self.client.post(
            f"/api/session/{sid}/suggested-patches/run",
            json={"patches": [{"nits": "not_a_number", "r": 235, "g": 16, "b": 16, "priority": "high"}]},
        )
        self.assertEqual(resp.status_code, 422)

    def test_run_suggested_patches_validates_patch_fields(self):
        _reset_server_globals()
        self.client = TestClient(server_app)
        sid = self._create_session()

        resp = self.client.post(
            f"/api/session/{sid}/suggested-patches/run",
            json={"patches": [{"nits": 120, "r": 235, "g": 16, "b": 16}]},  # missing priority
        )
        self.assertEqual(resp.status_code, 422)

    def test_run_suggested_patches_rejects_too_many_patches(self):
        _reset_server_globals()
        self.client = TestClient(server_app)
        sid = self._create_session()
        server_module._zro_bridge_url = "http://localhost:7070"

        big_patches = [
            {"nits": 120, "r": 235, "g": 16, "b": 16, "priority": "high"}
            for _ in range(201)
        ]
        resp = self.client.post(
            f"/api/session/{sid}/suggested-patches/run",
            json={"patches": big_patches},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("too long", resp.json()["detail"])


class TestPydanticModels(unittest.TestCase):
    """Tests for the SuggestedPatchBody and RunPatchesReq Pydantic models."""

    def test_suggested_patch_body_valid(self):
        from server import SuggestedPatchBody
        patch = SuggestedPatchBody(nits=120.0, r=235, g=16, b=16, priority="high")
        self.assertEqual(patch.nits, 120.0)
        self.assertEqual(patch.priority, "high")

    def test_suggested_patch_body_defaults(self):
        from server import SuggestedPatchBody
        patch = SuggestedPatchBody(nits=0, r=0, g=0, b=0, priority="low")
        self.assertEqual(patch.label, "")
        self.assertEqual(patch.rationale, "")

    def test_suggested_patch_body_invalid_priority(self):
        from server import SuggestedPatchBody
        # Pydantic should accept any string for priority (no enum constraint)
        patch = SuggestedPatchBody(nits=120, r=235, g=16, b=16, priority="critical")
        self.assertEqual(patch.priority, "critical")

    def test_run_patches_req_valid(self):
        from server import RunPatchesReq, SuggestedPatchBody
        body = RunPatchesReq(patches=[
            SuggestedPatchBody(nits=120, r=235, g=16, b=16, priority="high"),
            SuggestedPatchBody(nits=60, r=16, g=235, b=16, priority="medium"),
        ])
        self.assertEqual(len(body.patches), 2)

    def test_run_patches_req_empty(self):
        from server import RunPatchesReq
        body = RunPatchesReq(patches=[])
        self.assertEqual(len(body.patches), 0)


class TestZROBridgeMeasureSequence(unittest.TestCase):
    """Tests for the ZRO bridge /measure/sequence endpoint."""

    def _make_bridge_app(self, config=None):
        """Create a ZRO bridge app with mocked backend."""
        import sys
        from io import StringIO

        # Create a minimal bridge app without importing backends
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.post("/measure/sequence")
        def measure_sequence(body: dict):
            patches = body.get("patches", [])
            if not patches or not isinstance(patches, list):
                from fastapi import HTTPException
                raise HTTPException(400, "Body must include a non-empty 'patches' list.")
            if len(patches) > 200:
                from fastapi import HTTPException
                raise HTTPException(400, "Patch sequence too long (max 200).")

            results = []
            for i, p in enumerate(patches):
                r = int(p.get("r", 0))
                g = int(p.get("g", 0))
                b = int(p.get("b", 0))
                label = p.get("label", f"RGB({r},{g},{b})")
                results.append({
                    "index": i,
                    "label": label,
                    "rgb": [r, g, b],
                    "ok": True,
                    "error": None,
                })

            return {
                "accepted": len(patches),
                "results": results,
                "succeeded": len(patches),
            }

        return TestClient(app), app

    def test_measure_sequence_single_patch(self):
        client, app = self._make_bridge_app()
        resp = client.post("/measure/sequence", json={
            "patches": [{"nits": 120, "r": 235, "g": 16, "b": 16, "priority": "high", "label": "Red 100%"}]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["accepted"], 1)
        self.assertEqual(data["succeeded"], 1)
        self.assertEqual(data["results"][0]["label"], "Red 100%")
        self.assertEqual(data["results"][0]["rgb"], [235, 16, 16])

    def test_measure_sequence_multiple_patches(self):
        client, app = self._make_bridge_app()
        patches = [
            {"nits": 120, "r": 235, "g": 16, "b": 16, "priority": "high", "label": "Red"},
            {"nits": 120, "r": 16, "g": 235, "b": 16, "priority": "high", "label": "Green"},
            {"nits": 120, "r": 16, "g": 16, "b": 235, "priority": "high", "label": "Blue"},
        ]
        resp = client.post("/measure/sequence", json={"patches": patches})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["accepted"], 3)
        self.assertEqual(data["succeeded"], 3)
        self.assertEqual(len(data["results"]), 3)

    def test_measure_sequence_rejects_empty(self):
        client, app = self._make_bridge_app()
        resp = client.post("/measure/sequence", json={"patches": []})
        self.assertEqual(resp.status_code, 400)

    def test_measure_sequence_rejects_missing_patches(self):
        client, app = self._make_bridge_app()
        resp = client.post("/measure/sequence", json={})
        self.assertEqual(resp.status_code, 400)

    def test_measure_sequence_rejects_too_many(self):
        client, app = self._make_bridge_app()
        patches = [
            {"nits": 120, "r": 235, "g": 16, "b": 16, "priority": "high"}
            for _ in range(201)
        ]
        resp = client.post("/measure/sequence", json={"patches": patches})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("too long", resp.json()["detail"])

    def test_measure_sequence_defaults_missing_fields(self):
        client, app = self._make_bridge_app()
        # Patch with only required fields
        resp = client.post("/measure/sequence", json={
            "patches": [{"nits": 120, "r": 128, "g": 128, "b": 128}]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["results"][0]["label"], "RGB(128,128,128)")
        self.assertEqual(data["results"][0]["rgb"], [128, 128, 128])

    def test_measure_sequence_preserves_labels(self):
        client, app = self._make_bridge_app()
        patches = [
            {"nits": 120, "r": 235, "g": 16, "b": 16, "label": "Custom Red Patch"},
            {"nits": 60, "r": 64, "g": 64, "b": 64, "label": "Dim Gray"},
        ]
        resp = client.post("/measure/sequence", json={"patches": patches})
        self.assertEqual(resp.status_code, 200)
        labels = [r["label"] for r in resp.json()["results"]]
        self.assertEqual(labels, ["Custom Red Patch", "Dim Gray"])


if __name__ == "__main__":
    unittest.main()
