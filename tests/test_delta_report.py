"""Tests for Before/After Delta Report (issue #168) and Predictive Patch Density (issue #173)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from calibrator import Measurement
from calibrator.reports import comparison_payload
import server as server_module
from server import app, _sessions


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _reset_globals():
    _sessions.clear()
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


@pytest.fixture(autouse=True)
def clear_sessions():
    _reset_globals()
    yield
    _reset_globals()


@pytest.fixture
def client():
    return TestClient(app)


def _make_session_with_measurements(client, mode="SDR"):
    """Create a session with SDR measurements ready for report generation."""
    resp = client.post("/api/session", json={"tv_key": "u8g"})
    sid = resp.json()["id"]
    client.post(f"/api/session/{sid}/mode", json={"mode": mode})
    client.post(f"/api/session/{sid}/prepared")

    measurements = [
        Measurement(
            x=0.3127, y=0.3290,
            Y=float(pct),
            X=float(pct) * 0.95,
            Z=float(pct) * 1.09,
            label=f"{pct}% Gray",
            stimulus_rgb=(pct * 2, pct * 2, pct * 2),
        )
        for pct in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    ]

    _sessions[sid]["pre_measurements"] = measurements
    _sessions[sid]["post_measurements"] = [
        Measurement(
            x=0.3127, y=0.3290,
            Y=float(pct) * 1.02,
            X=float(pct) * 0.97,
            Z=float(pct) * 1.07,
            label=f"{pct}% Gray",
            stimulus_rgb=(pct * 2, pct * 2, pct * 2),
        )
        for pct in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    ]
    _sessions[sid]["peak_luminance"] = 500.0

    return sid


# ── comparison_payload unit tests ─────────────────────────────────────────────

class TestComparisonPayload:
    def test_comparison_payload_structure(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)

        sess_a = _sessions[sid_a]
        sess_b = _sessions[sid_b]

        result = comparison_payload(sess_a, sess_b)

        assert "session_a" in result
        assert "session_b" in result
        assert "deltas" in result
        assert "tv_mismatch" in result
        assert "mode_mismatch" in result

    def test_comparison_same_session_zero_deltas(self, client):
        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]

        result = comparison_payload(sess, sess)
        deltas = result["deltas"]

        # Same session: all non-None deltas should be 0.0
        for key, val in deltas.items():
            if val is not None:
                assert abs(val) < 0.01, f"Expected ~0 delta for {key}, got {val}"

    def test_comparison_no_tv_mismatch_same_key(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)

        result = comparison_payload(_sessions[sid_a], _sessions[sid_b])
        assert result["tv_mismatch"] is False
        assert result["mode_mismatch"] is False

    def test_comparison_flags_mode_mismatch(self, client):
        sid_a = _make_session_with_measurements(client, mode="SDR")
        sid_b = _make_session_with_measurements(client, mode="HDR10")

        result = comparison_payload(_sessions[sid_a], _sessions[sid_b])
        assert result["mode_mismatch"] is True

    def test_comparison_deltas_are_b_minus_a(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)
        # Inflate session B's peak luminance to create a detectable delta
        _sessions[sid_b]["peak_luminance"] = 600.0

        result = comparison_payload(_sessions[sid_a], _sessions[sid_b])
        delta_peak = result["deltas"]["peak_luminance"]
        assert delta_peak is not None
        assert abs(delta_peak - 100.0) < 1.0  # 600 - 500 = 100

    def test_comparison_reports_include_tv_name(self, client):
        sid_a = _make_session_with_measurements(client)
        result = comparison_payload(_sessions[sid_a], _sessions[sid_a])
        assert result["session_a"]["tv"]
        assert result["session_b"]["tv"]


# ── GET /api/report/compare ────────────────────────────────────────────────────

class TestCompareEndpoint:
    def test_compare_returns_json(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)

        resp = client.get(f"/api/report/compare?a={sid_a}&b={sid_b}")
        assert resp.status_code == 200
        data = resp.json()
        assert "deltas" in data
        assert "session_a" in data
        assert "session_b" in data

    def test_compare_returns_html(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)

        resp = client.get(f"/api/report/compare?a={sid_a}&b={sid_b}&format=html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Before / After Delta Report" in resp.text

    def test_compare_404_for_unknown_session_a(self, client):
        sid_b = _make_session_with_measurements(client)
        resp = client.get(f"/api/report/compare?a=doesnotexist&b={sid_b}")
        assert resp.status_code == 404

    def test_compare_404_for_unknown_session_b(self, client):
        sid_a = _make_session_with_measurements(client)
        resp = client.get(f"/api/report/compare?a={sid_a}&b=doesnotexist")
        assert resp.status_code == 404

    def test_compare_html_includes_delta_section(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)

        resp = client.get(f"/api/report/compare?a={sid_a}&b={sid_b}&format=html")
        assert "Key Metrics" in resp.text
        assert "Pre-Cal Avg" in resp.text
        assert "Post-Cal Avg" in resp.text

    def test_compare_same_session_is_valid(self, client):
        sid = _make_session_with_measurements(client)
        resp = client.get(f"/api/report/compare?a={sid}&b={sid}")
        assert resp.status_code == 200

    def test_compare_without_llm_delta_summary_returns_null(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)

        resp = client.post(
            f"/api/report/compare/delta_summary?a={sid_a}&b={sid_b}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] is None


# ── GET /api/session/{sid}/suggested-patches ──────────────────────────────────

class TestSuggestedPatches:
    def test_suggested_patches_no_llm_returns_null(self, client):
        sid = _make_session_with_measurements(client)
        resp = client.get(f"/api/session/{sid}/suggested-patches")
        assert resp.status_code == 200
        data = resp.json()
        assert data["optimization"] is None
        assert "reason" in data

    def test_suggested_patches_no_measurements_returns_400(self, client):
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{sid}/prepared")
        # Ensure no measurements are present and LLM is configured to bypass the LLM gate
        _sessions[sid]["llm_config"] = {"endpoint": "http://localhost:4000", "model": "test"}

        resp = client.get(f"/api/session/{sid}/suggested-patches")
        assert resp.status_code == 400

    def test_suggested_patches_invalid_budget_returns_400(self, client):
        sid = _make_session_with_measurements(client)
        resp = client.get(f"/api/session/{sid}/suggested-patches?budget=0")
        assert resp.status_code == 400

        resp = client.get(f"/api/session/{sid}/suggested-patches?budget=201")
        assert resp.status_code == 400

    def test_suggested_patches_404_for_unknown_session(self, client):
        resp = client.get("/api/session/doesnotexist/suggested-patches")
        assert resp.status_code == 404

    def test_suggested_patches_run_with_empty_list_returns_400(self, client):
        sid = _make_session_with_measurements(client)
        resp = client.post(
            f"/api/session/{sid}/suggested-patches/run",
            json={"patches": []},
        )
        assert resp.status_code == 400

    def test_suggested_patches_run_bridge_unreachable_returns_502(self, client):
        server_module._zro_bridge_url = "http://localhost:19999"  # nothing listening here
        sid = _make_session_with_measurements(client)
        resp = client.post(
            f"/api/session/{sid}/suggested-patches/run",
            json={"patches": [{"nits": 100.0, "r": 200, "g": 200, "b": 200, "priority": "high", "label": "Test"}]},
        )
        assert resp.status_code == 502


# ── calcore/patch_planner unit tests ──────────────────────────────────────────

class TestPatchPlanner:
    def test_plan_patches_returns_residuals(self):
        from calcore.patch_planner import plan_patches

        gray_rows = [
            {"label": "10% Gray", "dE2000": 5.2, "gamma": 2.1},
            {"label": "50% Gray", "dE2000": 1.1, "gamma": 2.2},
            {"label": "90% Gray", "dE2000": 3.8, "gamma": 2.3},
        ]
        color_rows = [
            {"label": "Red 100%", "dE2000": 4.0, "dE2000_chroma_only": 3.0},
            {"label": "Blue 75%", "dE2000": 1.5, "dE2000_chroma_only": 1.0},
        ]
        result = plan_patches(gray_rows, color_rows, budget=15)

        assert "residuals" in result
        assert "patch_budget" in result
        assert result["patch_budget"] == 15
        assert "grayscale_by_error" in result["residuals"]
        assert "color_by_error" in result["residuals"]

    def test_plan_patches_sorts_by_error_descending(self):
        from calcore.patch_planner import plan_patches

        gray_rows = [
            {"label": "A", "dE2000": 1.0},
            {"label": "B", "dE2000": 5.0},
            {"label": "C", "dE2000": 3.0},
        ]
        result = plan_patches(gray_rows, [], budget=30)
        sorted_labels = [r["label"] for r in result["residuals"]["grayscale_by_error"]]
        assert sorted_labels == ["B", "C", "A"]

    def test_suggested_patch_from_dict_round_trips(self):
        from calcore.patch_planner import SuggestedPatch

        p = SuggestedPatch(nits=100.0, r=200, g=200, b=200, priority="high", label="Test", rationale="Because")
        d = p.to_dict()
        p2 = SuggestedPatch.from_dict(d)
        assert p2.nits == p.nits
        assert p2.r == p.r
        assert p2.priority == p.priority
        assert p2.label == p.label

    def test_patch_optimization_to_dict(self):
        from calcore.patch_planner import PatchOptimization, SuggestedPatch

        opt = PatchOptimization(
            patches=[SuggestedPatch(nits=80.0, r=200, g=200, b=200, priority="high", label="G80")],
            rationale="Dense 60-85% range",
            confidence=0.85,
            auto_apply=True,
        )
        d = opt.to_dict()
        assert d["patch_count"] == 1
        assert d["auto_apply"] is True
        assert d["confidence"] == 0.85
        assert d["patches"][0]["label"] == "G80"
