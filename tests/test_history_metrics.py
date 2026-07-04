"""Tests for #268 — history/comparison endpoint returns None for all computed metrics.

The /api/report/history/{tv_key} endpoint reads serialized session entries that may not
have computed metric fields (post_grayscale_avg_de, gamma_avg, wb_avg_de, cms_avg_de).
These fields are only computed on-the-fly by report_payload(). The history endpoint
should compute these metrics for entries that are missing them.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from calibrator import Measurement
from calibrator.history import history_summary, load_history, record_session
import server as server_module
from server import app, _sessions


@pytest.fixture(autouse=True)
def clear_sessions():
    _sessions.clear()
    server_module._watched_session.set(None)
    yield
    _sessions.clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session_id(client):
    """Create a session and return the ID."""
    resp = client.post("/api/session", json={"tv_key": "u8g"})
    assert resp.status_code == 200
    return resp.json()["id"]


def _make_target():
    """Create a mock calibration target."""
    return type("T", (), {
        "gamut": "bt709",
        "eotf": "bt1886",
        "peak_luminance_nits": 500,
        "white_point_xy": (0.3127, 0.3290),
        "gamma": 2.4,
    })()


def _make_measurement(x=0.3127, y=0.3290, Y=50.0, label="Test"):
    """Create a basic measurement."""
    return Measurement(
        x=x, y=y, Y=Y, X=48.3, Z=55.1,
        stimulus_rgb=(128, 128, 128), label=label,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


class TestHistoryEndpointReturnsComputedMetrics:
    """#268: History endpoint should compute metrics for entries missing them."""

    def test_history_entry_without_metrics_is_computed(self, client, session_id, monkeypatch):
        """History entry missing computed fields should get them from report_payload."""
        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = _make_target()
        _sessions[session_id]["pre_measurements"] = [_make_measurement()]
        _sessions[session_id]["post_measurements"] = [_make_measurement()]
        _sessions[session_id]["wb_measurements"] = [_make_measurement()]
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = [_make_measurement()]
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        def mock_load_history(tv_key, limit=10):
            return [{
                "session_id": session_id,
                "date": datetime.now(timezone.utc).isoformat(),
                "mode": "SDR",
            }]

        monkeypatch.setattr(server_module, "_load_history", mock_load_history)
        monkeypatch.setattr(server_module, "_load_baseline", lambda tv_key: None)

        resp = client.get(f"/api/report/history/{_sessions[session_id]['tv_key']}")
        assert resp.status_code == 200
        data = resp.json()
        sessions = data["sessions"]
        assert len(sessions) == 1

        entry = sessions[0]
        metrics = [entry.get("avg_de"), entry.get("gamma_avg"), entry.get("wb_avg_de"), entry.get("cms_avg_de")]
        assert any(m is not None for m in metrics), f"All metrics are None: {metrics}"

    def test_baseline_without_metrics_is_computed(self, client, session_id, monkeypatch):
        """Baseline entry missing computed fields should get them from report_payload."""
        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = _make_target()
        _sessions[session_id]["pre_measurements"] = [_make_measurement()]
        _sessions[session_id]["post_measurements"] = [_make_measurement()]
        _sessions[session_id]["wb_measurements"] = [_make_measurement()]
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = [_make_measurement()]
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        def mock_load_baseline(tv_key):
            return {
                "session_id": session_id,
                "date": datetime.now(timezone.utc).isoformat(),
                "mode": "SDR",
            }

        monkeypatch.setattr(server_module, "_load_history", lambda tv_key, limit=10: [])
        monkeypatch.setattr(server_module, "_load_baseline", mock_load_baseline)

        resp = client.get(f"/api/report/history/{_sessions[session_id]['tv_key']}")
        assert resp.status_code == 200
        data = resp.json()
        sessions = data["sessions"]
        assert len(sessions) == 1

        entry = sessions[0]
        metrics = [entry.get("avg_de"), entry.get("gamma_avg"), entry.get("wb_avg_de"), entry.get("cms_avg_de")]
        assert any(m is not None for m in metrics), f"All baseline metrics are None: {metrics}"

    def test_history_entry_with_existing_metrics_is_not_recomputed(self, client, session_id, monkeypatch):
        """History entry that already has computed fields should not trigger recomputation."""
        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = _make_target()
        _sessions[session_id]["pre_measurements"] = [_make_measurement()]
        _sessions[session_id]["post_measurements"] = [_make_measurement()]
        _sessions[session_id]["wb_measurements"] = []
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = []
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        call_count = [0]
        old_report_payload = server_module._report_payload

        def counting_report_payload(session):
            call_count[0] += 1
            return old_report_payload(session)

        monkeypatch.setattr(server_module, "_report_payload", counting_report_payload)

        def mock_load_history(tv_key, limit=10):
            return [{
                "session_id": session_id,
                "date": datetime.now(timezone.utc).isoformat(),
                "mode": "SDR",
                "post_grayscale_avg_de": 1.5,
                "gamma_avg": 2.3,
                "wb_avg_de": 0.8,
                "cms_avg_de": 1.2,
                "peak_luminance": 200.0,
            }]

        monkeypatch.setattr(server_module, "_load_history", mock_load_history)
        monkeypatch.setattr(server_module, "_load_baseline", lambda tv_key: None)

        resp = client.get(f"/api/report/history/{_sessions[session_id]['tv_key']}")
        assert resp.status_code == 200
        data = resp.json()
        sessions = data["sessions"]
        assert len(sessions) == 1

        entry = sessions[0]
        assert entry["avg_de"] == 1.5
        assert entry["gamma_avg"] == 2.3

        # report_payload should not have been called (metrics already exist)
        assert call_count[0] == 0

    def test_history_endpoint_handles_missing_session_gracefully(self, client, session_id, monkeypatch):
        """History entry for a missing session should not crash the endpoint."""
        def mock_load_history(tv_key, limit=10):
            return [{
                "session_id": "non-existent-session-id",
                "date": datetime.now(timezone.utc).isoformat(),
                "mode": "SDR",
            }]

        monkeypatch.setattr(server_module, "_load_history", mock_load_history)
        monkeypatch.setattr(server_module, "_load_baseline", lambda tv_key: None)

        resp = client.get(f"/api/report/history/{_sessions[session_id]['tv_key'] if session_id else 'u8g'}")
        assert resp.status_code == 200


class TestHistoryEndpointMetricValuesAreCorrect:
    """#268: Computed metrics should match report_payload values."""

    def test_computed_metrics_match_report_values(self, client, session_id, monkeypatch):
        """Computed metrics should match what report_payload produces."""
        _sessions[session_id]["step"] = "report"
        _sessions[session_id]["target"] = _make_target()
        _sessions[session_id]["pre_measurements"] = [_make_measurement()]
        _sessions[session_id]["post_measurements"] = [_make_measurement()]
        _sessions[session_id]["wb_measurements"] = [_make_measurement()]
        _sessions[session_id]["gamma_measurements"] = []
        _sessions[session_id]["cms_measurements"] = [_make_measurement()]
        _sessions[session_id]["lum_measurements"] = []
        _sessions[session_id]["peak_luminance"] = 200.0

        expected_report = server_module._report_payload(_sessions[session_id])

        def mock_load_history(tv_key, limit=10):
            return [{
                "session_id": session_id,
                "date": datetime.now(timezone.utc).isoformat(),
                "mode": "SDR",
            }]

        monkeypatch.setattr(server_module, "_load_history", mock_load_history)
        monkeypatch.setattr(server_module, "_load_baseline", lambda tv_key: None)

        resp = client.get(f"/api/report/history/{_sessions[session_id]['tv_key']}")
        assert resp.status_code == 200
        data = resp.json()
        sessions = data["sessions"]

        entry = sessions[0]
        assert entry["avg_de"] == expected_report["post_cal"]["avg_de"]


class TestRecordSessionIdempotent:
    """record_session must not duplicate entries with the same session_id."""

    @pytest.fixture(autouse=True)
    def _history_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TVCAL_HISTORY_DIR", str(tmp_path))

    def _report(self) -> dict:
        return {
            "pre_cal": {"avg_de": 5.0},
            "post_cal": {"avg_de": 1.2},
            "gamma": {"avg_gamma": 2.2},
            "peak_luminance": 500.0,
            "improvement_pct": 76.0,
            "white_balance": {"avg_de": 0.8},
            "color_tuner": {"avg_de": 1.5},
        }

    def test_second_call_updates_not_duplicates(self):
        record_session(
            tv_key="u8g", session_id="sid-1", mode="SDR", report=self._report(),
            wb_final={"two_point": {"offset_r": 0}}, cms_final={"red_hue": 0},
        )
        record_session(
            tv_key="u8g", session_id="sid-2", mode="SDR", report=self._report(),
        )
        entries_before = load_history("u8g", limit=100)
        assert len(entries_before) == 2

        # Second call with same session_id should update, not duplicate
        updated_report = self._report()
        updated_report["post_cal"]["avg_de"] = 0.9
        record_session(
            tv_key="u8g", session_id="sid-1", mode="SDR", report=updated_report,
            wb_final={"two_point": {"offset_r": 1}}, cms_final={"red_hue": 5},
        )
        entries = load_history("u8g", limit=100)
        assert len(entries) == 2, "record_session should not duplicate same session_id"
        sid1 = [e for e in entries if e["session_id"] == "sid-1"]
        assert len(sid1) == 1
        assert sid1[0]["post_grayscale_avg_de"] == 0.9
        assert sid1[0]["wb_final"] == {"two_point": {"offset_r": 1}}

    def test_first_call_creates_entry(self):
        record_session(
            tv_key="u8g", session_id="sid-3", mode="SDR", report=self._report(),
        )
        entries = load_history("u8g", limit=100)
        assert len(entries) == 1
        assert entries[0]["session_id"] == "sid-3"


class TestHistorySummarySessionCount:
    """#578: history_summary()["session_count"] must not saturate at the display cap."""

    @pytest.fixture(autouse=True)
    def _history_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TVCAL_HISTORY_DIR", str(tmp_path))

    def _report(self) -> dict:
        return {
            "pre_cal": {"avg_de": 5.0},
            "post_cal": {"avg_de": 1.2},
            "gamma": {"avg_gamma": 2.2},
            "peak_luminance": 500.0,
            "improvement_pct": 76.0,
            "white_balance": {"avg_de": 0.8},
            "color_tuner": {"avg_de": 1.5},
        }

    def test_session_count_not_capped_at_display_limit(self):
        for i in range(12):
            record_session(
                tv_key="u8g", session_id=f"sid-{i}", mode="SDR", report=self._report(),
            )
        assert history_summary("u8g")["session_count"] == 12
