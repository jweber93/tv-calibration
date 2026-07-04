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
            timestamp="2024-01-01T10:00:00",
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
            timestamp="2024-01-01T10:00:00",
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




class TestReportPayloadSessionId:
    def test_report_payload_includes_session_id(self, client):
        from calibrator.reports import report_payload
        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]
        result = report_payload(sess)
        assert "session_id" in result
        assert result["session_id"] == sid

    def test_report_payload_session_id_empty_when_missing(self, client):
        from calibrator.reports import report_payload
        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]
        del sess["id"]
        result = report_payload(sess)
        assert result["session_id"] == ""


class TestComparisonPayloadSessionIds:
    def test_comparison_payload_includes_session_ids(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)
        result = comparison_payload(_sessions[sid_a], _sessions[sid_b])
        assert result["session_id_a"] == sid_a
        assert result["session_id_b"] == sid_b

    def test_comparison_endpoint_includes_session_ids(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)
        resp = client.get(f"/api/report/compare?a={sid_a}&b={sid_b}")
        data = resp.json()
        assert data["session_id_a"] == sid_a
        assert data["session_id_b"] == sid_b


# ── Edge-case comparison tests (issue #277) ───────────────────────────────────

class TestComparisonEdgeCases:
    def test_comparison_session_a_no_pre_cal(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)
        _sessions[sid_a]["pre_measurements"] = []

        result = comparison_payload(_sessions[sid_a], _sessions[sid_b])
        deltas = result["deltas"]

        assert deltas["pre_cal_avg_de"] is None
        assert deltas["pre_cal_max_de"] is None
        assert result["session_a"]["report"]["pre_cal"]["avg_de"] is None

    def test_comparison_session_b_no_post_cal(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)
        _sessions[sid_b]["post_measurements"] = []

        result = comparison_payload(_sessions[sid_a], _sessions[sid_b])
        deltas = result["deltas"]

        assert deltas["post_cal_avg_de"] is None
        assert deltas["post_cal_max_de"] is None
        assert result["session_b"]["report"]["post_cal"]["avg_de"] is None

    def test_comparison_both_sessions_zero_delta_e(self, client):
        from calibrator.reports import report_payload
        from calibrator.utils import stimulus_pct_from_code_value

        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]
        target = sess["target"]
        signal_range = sess.get("signal_range", "full")

        perfect_measurements = [
            Measurement(
                x=target.white_point_xy[0],
                y=target.white_point_xy[1],
                Y=target.peak_luminance_nits * (stimulus_pct_from_code_value(pct * 2, signal_range) / 100.0) ** target.gamma,
                X=0.0, Z=0.0,
                label=f"{pct}% Gray",
                stimulus_rgb=(pct * 2, pct * 2, pct * 2),
                timestamp="2024-01-01T10:00:00",
            )
            for pct in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        ]
        sess["pre_measurements"] = perfect_measurements
        sess["post_measurements"] = perfect_measurements

        report = report_payload(sess)
        assert report["pre_cal"]["avg_de"] == 0.0
        assert report["improvement_pct"] is None

        result = comparison_payload(sess, sess)
        assert result["deltas"]["improvement_pct"] is None

    def test_comparison_one_session_empty_one_complete(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)
        _sessions[sid_a]["pre_measurements"] = []
        _sessions[sid_a]["post_measurements"] = []

        result = comparison_payload(_sessions[sid_a], _sessions[sid_b])
        deltas = result["deltas"]

        assert deltas["pre_cal_avg_de"] is None
        assert deltas["post_cal_avg_de"] is None
        assert deltas["pre_cal_max_de"] is None
        assert deltas["post_cal_max_de"] is None

    def test_comparison_only_invalid_measurements(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)

        invalid_measurements = [
            Measurement(
                x=float("nan"), y=float("nan"),
                Y=float("nan"),
                X=float("nan"),
                Z=float("nan"),
                label="Invalid",
                stimulus_rgb=(128, 128, 128),
                timestamp="2024-01-01T10:00:00",
            )
        ]
        _sessions[sid_a]["pre_measurements"] = invalid_measurements
        _sessions[sid_a]["post_measurements"] = invalid_measurements

        result = comparison_payload(_sessions[sid_a], _sessions[sid_b])
        report_a = result["session_a"]["report"]

        assert report_a["pre_cal"]["avg_de"] is None
        assert report_a["pre_cal"]["invalid_count"] == 1
        assert report_a["improvement_pct"] is None


class TestReportUsesLatestPassOnly:
    """Issue #577: report_payload must reduce each bucket to the latest pass
    before averaging, matching the live grayscale view / quality gate."""

    def test_report_uses_latest_grayscale_pass(self, client):
        from calibrator.reports import report_payload

        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]

        bad_pass = [
            Measurement(
                x=0.3127 + 0.05, y=0.3290 + 0.05,
                Y=float(pct), X=float(pct) * 0.95, Z=float(pct) * 1.09,
                label=f"{pct}% Gray",
                stimulus_rgb=(pct * 2, pct * 2, pct * 2),
                timestamp="2024-01-01T10:00:00",
            )
            for pct in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        ]
        good_pass = [
            Measurement(
                x=0.3127, y=0.3290,
                Y=float(pct), X=float(pct) * 0.95, Z=float(pct) * 1.09,
                label=f"{pct}% Gray",
                stimulus_rgb=(pct * 2, pct * 2, pct * 2),
                timestamp="2024-01-01T11:00:00",
            )
            for pct in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        ]
        good_only_sess = dict(sess)
        good_only_sess["pre_measurements"] = good_pass
        good_only_report = report_payload(good_only_sess)

        sess["pre_measurements"] = bad_pass + good_pass
        report = report_payload(sess)

        # The combined bucket (bad pass + good re-measured pass) must report the
        # same avg_de as the good pass alone, not a blend of both passes.
        assert report["pre_cal"]["avg_de"] == good_only_report["pre_cal"]["avg_de"]
        assert report["pre_cal"]["max_de"] == good_only_report["pre_cal"]["max_de"]
        assert len(report["pre_cal"]["measurements"]) == len(good_pass)

    def test_report_uses_latest_cms_reading_per_colour(self, client):
        from calibrator.reports import report_payload

        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]
        target = sess["target"]

        stale = Measurement(
            x=target.white_point_xy[0] + 0.05, y=target.white_point_xy[1] + 0.05,
            Y=100.0, X=90.0, Z=100.0,
            label="Red 100%",
            stimulus_rgb=(255, 0, 0),
        )
        fresh = Measurement(
            x=target.white_point_xy[0], y=target.white_point_xy[1],
            Y=100.0, X=90.0, Z=100.0,
            label="Red 100%",
            stimulus_rgb=(255, 0, 0),
        )
        sess["cms_measurements"] = [stale, fresh]

        report = report_payload(sess)
        assert report["color_tuner"]["avg_de"] == 0.0
        assert report["color_tuner"]["max_de"] == 0.0

    def test_report_uses_latest_gamma_pass(self, client):
        from calibrator.reports import report_payload
        from calibrator.session import GAMMA_TRACKING_LEVELS

        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]

        level = GAMMA_TRACKING_LEVELS[0]
        code_value = round(level / 100.0 * 255)
        stale = Measurement(
            x=0.3127, y=0.3290, Y=1.0, X=0.95, Z=1.09,
            label=f"Gamma {level}%",
            stimulus_rgb=(code_value, code_value, code_value),
            timestamp="2024-01-01T10:00:00",
        )
        fresh = Measurement(
            x=0.3127, y=0.3290, Y=50.0, X=0.95, Z=1.09,
            label=f"Gamma {level}%",
            stimulus_rgb=(code_value, code_value, code_value),
            timestamp="2024-01-01T11:00:00",
        )
        sess["gamma_measurements"] = [stale, fresh]

        report = report_payload(sess)
        assert len(report["gamma_measurements"]) == 1
        assert report["gamma_measurements"][0]["Y"] == 50.0

    def test_report_falls_back_to_whole_bucket_when_no_timestamps(self, client):
        """Legacy/pre-#577 data may have no timestamps at all. latest_grayscale_pass
        and latest_gamma_pass return empty in that case (they can't anchor a
        backward walk), so report_payload must fall back to treating the whole
        bucket as a single pass rather than reporting empty/None stats."""
        from calibrator.reports import report_payload
        from calibrator.session import GAMMA_TRACKING_LEVELS

        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]

        no_ts_grayscale = [
            Measurement(
                x=0.3127, y=0.3290,
                Y=float(pct), X=float(pct) * 0.95, Z=float(pct) * 1.09,
                label=f"{pct}% Gray",
                stimulus_rgb=(pct * 2, pct * 2, pct * 2),
            )
            for pct in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        ]
        sess["pre_measurements"] = no_ts_grayscale

        level = GAMMA_TRACKING_LEVELS[0]
        code_value = round(level / 100.0 * 255)
        no_ts_gamma = [
            Measurement(
                x=0.3127, y=0.3290, Y=50.0, X=0.95, Z=1.09,
                label=f"Gamma {level}%",
                stimulus_rgb=(code_value, code_value, code_value),
            )
        ]
        sess["gamma_measurements"] = no_ts_gamma

        report = report_payload(sess)
        assert report["pre_cal"]["avg_de"] is not None
        assert len(report["pre_cal"]["measurements"]) == len(no_ts_grayscale)
        assert len(report["gamma_measurements"]) == 1


class TestReportPayloadEdgeCases:
    def test_report_payload_empty_measurements_no_crash(self, client):
        from calibrator.reports import report_payload
        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]
        sess["pre_measurements"] = []
        sess["post_measurements"] = []
        sess["wb_measurements"] = []
        sess["gamma_measurements"] = []
        sess["cms_measurements"] = []

        result = report_payload(sess)

        assert result["pre_cal"]["avg_de"] is None
        assert result["pre_cal"]["max_de"] is None
        assert result["pre_cal"]["measurements"] == []
        assert result["post_cal"]["avg_de"] is None
        assert result["improvement_pct"] is None
        assert result["white_balance"]["avg_de"] is None
        assert result["color_tuner"]["avg_de"] is None
        assert result["gamma"]["avg_gamma"] is None

    def test_report_payload_pre_zero_post_nonzero_improvement_none(self, client):
        from calibrator.reports import report_payload
        from calibrator.utils import stimulus_pct_from_code_value

        sid = _make_session_with_measurements(client)
        sess = _sessions[sid]
        target = sess["target"]
        signal_range = sess.get("signal_range", "full")

        perfect = Measurement(
            x=target.white_point_xy[0],
            y=target.white_point_xy[1],
            Y=target.peak_luminance_nits * (stimulus_pct_from_code_value(100, signal_range) / 100.0) ** target.gamma,
            X=0.0, Z=0.0,
            label="50% Gray",
            stimulus_rgb=(100, 100, 100),
            timestamp="2024-01-01T10:00:00",
        )
        slightly_off = Measurement(
            x=target.white_point_xy[0] + 0.01,
            y=target.white_point_xy[1] + 0.01,
            Y=target.peak_luminance_nits * (stimulus_pct_from_code_value(102, signal_range) / 100.0) ** target.gamma,
            X=0.0, Z=0.0,
            label="51% Gray",
            stimulus_rgb=(102, 102, 102),
            timestamp="2024-01-01T10:00:00",
        )

        sess["pre_measurements"] = [perfect]
        sess["post_measurements"] = [slightly_off]

        result = report_payload(sess)
        assert result["pre_cal"]["avg_de"] == 0.0
        assert result["improvement_pct"] is None

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

    def test_compare_empty_session_returns_200_with_none_deltas(self, client):
        sid_a = _make_session_with_measurements(client)
        sid_b = _make_session_with_measurements(client)
        _sessions[sid_a]["pre_measurements"] = []
        _sessions[sid_a]["post_measurements"] = []

        resp = client.get(f"/api/report/compare?a={sid_a}&b={sid_b}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deltas"]["pre_cal_avg_de"] is None
        assert data["deltas"]["post_cal_avg_de"] is None
        assert data["session_a"]["report"]["pre_cal"]["avg_de"] is None


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
        server_module._zro_bridge.set("http://localhost:19999")  # nothing listening here
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
