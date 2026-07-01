"""Tests for the autocal run/stop/stream endpoints (Autocal roadmap Item 1d)."""
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from calcore.models import Measurement
from calibrator.guidance import target_nits_for_colour, target_xy_for_colour
import server as server_module
from server import app, _sessions


@pytest.fixture(autouse=True)
def clear_sessions():
    _sessions.clear()
    server_module._watched_session.set(None)
    server_module._prefs["autocal"] = dict(server_module._AUTOCAL_DEFAULTS)
    server_module._autocal_loops.clear()
    server_module._autocal_queues.clear()
    yield
    _sessions.clear()
    server_module._autocal_loops.clear()
    server_module._autocal_queues.clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session_id(client):
    resp = client.post("/api/session", json={"tv_key": "u8g"})
    sid = resp.json()["id"]
    client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
    return sid


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestAutocalRunValidation:
    def test_requires_target(self, client):
        resp = client.post("/api/session", json={"tv_key": "u8g"})
        sid = resp.json()["id"]
        resp = client.post(f"/api/session/{sid}/autocal/run", json={})
        assert resp.status_code == 400
        assert "target" in resp.json()["detail"].lower()

    def test_unknown_colour_rejected(self, client, session_id):
        resp = client.post(
            f"/api/session/{session_id}/autocal/run", json={"colours": ["Purple"]}
        )
        assert resp.status_code == 400
        assert "Purple" in resp.json()["detail"]

    def test_invalid_apply_mode_rejected(self, client, session_id):
        resp = client.post(
            f"/api/session/{session_id}/autocal/run", json={"apply_mode": "magic"}
        )
        assert resp.status_code == 400

    def test_damping_out_of_range_rejected(self, client, session_id):
        resp = client.post(
            f"/api/session/{session_id}/autocal/run", json={"damping": 1.5}
        )
        assert resp.status_code == 400

    def test_max_iterations_out_of_range_rejected(self, client, session_id):
        resp = client.post(
            f"/api/session/{session_id}/autocal/run", json={"max_iterations": 0}
        )
        assert resp.status_code == 400

    def test_unknown_session_404s(self, client):
        resp = client.post("/api/session/does-not-exist/autocal/run", json={})
        assert resp.status_code == 404

    def test_bridge_timeout_out_of_range_rejected(self, client, session_id):
        resp = client.post(
            f"/api/session/{session_id}/autocal/run", json={"bridge_timeout": 200}
        )
        assert resp.status_code == 400

    def test_bridge_poll_interval_out_of_range_rejected(self, client, session_id):
        resp = client.post(
            f"/api/session/{session_id}/autocal/run", json={"bridge_poll_interval": 10}
        )
        assert resp.status_code == 400


class TestAutocalRunLifecycle:
    def test_run_starts_and_rejects_concurrent_run(self, client, session_id):
        # Block the bridge trigger indefinitely so the background thread stays "running".
        block = threading.Event()

        def fake_measure(self, patch_obj):
            block.wait(timeout=5.0)
            raise TimeoutError("simulated: no measurement arrived")

        with patch("server._SessionCmsMeasurementSource.measure", fake_measure):
            resp = client.post(
                f"/api/session/{session_id}/autocal/run",
                json={"colours": ["Red"], "apply_mode": "manual", "max_iterations": 2},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "running"
            assert body["colours"] == ["Red"]
            assert body["apply_mode"] == "manual"

            assert _wait_until(lambda: session_id in server_module._autocal_loops)

            dupe = client.post(f"/api/session/{session_id}/autocal/run", json={})
            assert dupe.status_code == 409

            block.set()
            assert _wait_until(lambda: session_id not in server_module._autocal_loops)

    def test_stop_when_not_running(self, client, session_id):
        resp = client.post(f"/api/session/{session_id}/autocal/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_running"

    def test_stop_cancels_running_loop(self, client, session_id):
        block = threading.Event()
        cancelled_seen = threading.Event()

        def fake_measure(self, patch_obj):
            block.wait(timeout=5.0)
            raise TimeoutError("stopped before measuring")

        with patch("server._SessionCmsMeasurementSource.measure", fake_measure):
            client.post(
                f"/api/session/{session_id}/autocal/run",
                json={"colours": ["Red"], "max_iterations": 3},
            )
            assert _wait_until(lambda: session_id in server_module._autocal_loops)

            stop_resp = client.post(f"/api/session/{session_id}/autocal/stop")
            assert stop_resp.status_code == 200
            assert stop_resp.json()["status"] == "stopping"

            loop = server_module._autocal_loops.get(session_id)
            assert loop is None or loop.cancelled
            block.set()

    def test_run_response_echoes_and_passes_through_new_fields(self, client, session_id):
        block = threading.Event()

        def fake_measure(self, patch_obj):
            block.wait(timeout=5.0)
            raise TimeoutError("stopped")

        with patch("server._SessionCmsMeasurementSource.measure", fake_measure):
            resp = client.post(
                f"/api/session/{session_id}/autocal/run",
                json={
                    "colours": ["Red"],
                    "skip_stalled_controls": True,
                    "bridge_timeout": 15.0,
                    "bridge_poll_interval": 1.0,
                },
            )
            body = resp.json()
            assert body["skip_stalled_controls"] is True
            assert body["bridge_timeout"] == 15.0
            assert body["bridge_poll_interval"] == 1.0

            assert _wait_until(lambda: session_id in server_module._autocal_loops)
            loop = server_module._autocal_loops[session_id]
            assert loop.skip_stalled_controls is True

            block.set()
            _wait_until(lambda: session_id not in server_module._autocal_loops)


class TestAutocalStream:
    def test_unknown_session_404s(self, client):
        # A full happy-path read would block on the SSE generator's keep-alive
        # loop, which never terminates — matches the existing llm/stream tests,
        # which likewise only cover the 404-before-streaming-starts path.
        resp = client.get("/api/session/does-not-exist/autocal/stream")
        assert resp.status_code == 404


class TestAutocalHistory:
    def test_no_history_before_any_run(self, client, session_id):
        resp = client.get(f"/api/session/{session_id}/autocal/history")
        assert resp.status_code == 200
        assert resp.json() == {"available": False}

    def test_unknown_session_404s(self, client):
        resp = client.get("/api/session/does-not-exist/autocal/history")
        assert resp.status_code == 404

    def test_history_recorded_after_converged_run(self, client, session_id):
        session = server_module.store.get(session_id)
        target = session["target"]
        target_xy = target_xy_for_colour(target, "Red")
        target_nits = target_nits_for_colour(target, "Red")

        def fake_measure(self, patch_obj):
            return Measurement(
                x=target_xy[0], y=target_xy[1], Y=target_nits, label="Red", stimulus_rgb=(1023, 0, 0)
            )

        with patch("server._SessionCmsMeasurementSource.measure", fake_measure):
            client.post(
                f"/api/session/{session_id}/autocal/run",
                json={"colours": ["Red"], "max_iterations": 2},
            )
            assert _wait_until(lambda: session_id not in server_module._autocal_loops)

        resp = client.get(f"/api/session/{session_id}/autocal/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        red = body["colours"]["Red"]
        assert red["converged"] is True
        assert red["delta_e"] < 3.0
        assert red["history"] == []  # converged on the first measurement; no corrections needed


class TestAutocalPrefs:
    def test_get_prefs_includes_autocal_defaults(self, client):
        resp = client.get("/api/prefs")
        assert resp.json()["autocal"] == server_module._AUTOCAL_DEFAULTS

    def test_save_prefs_updates_autocal_fields(self, client):
        resp = client.post(
            "/api/prefs",
            json={
                "autocal_apply_mode": "adb",
                "autocal_damping": 0.5,
                "autocal_max_iterations": 12,
            },
        )
        assert resp.status_code == 200
        autocal = resp.json()["autocal"]
        assert autocal["apply_mode"] == "adb"
        assert autocal["damping"] == 0.5
        assert autocal["max_iterations"] == 12

    def test_save_prefs_rejects_bad_apply_mode(self, client):
        resp = client.post("/api/prefs", json={"autocal_apply_mode": "auto-pilot"})
        assert resp.status_code == 400

    def test_save_prefs_rejects_bad_damping(self, client):
        resp = client.post("/api/prefs", json={"autocal_damping": 0.0})
        assert resp.status_code == 400

    def test_save_prefs_updates_new_autocal_fields(self, client):
        resp = client.post(
            "/api/prefs",
            json={
                "autocal_skip_stalled_controls": True,
                "autocal_bridge_timeout": 45.0,
                "autocal_bridge_poll_interval": 1.5,
            },
        )
        assert resp.status_code == 200
        autocal = resp.json()["autocal"]
        assert autocal["skip_stalled_controls"] is True
        assert autocal["bridge_timeout"] == 45.0
        assert autocal["bridge_poll_interval"] == 1.5

    def test_save_prefs_rejects_bad_bridge_timeout(self, client):
        resp = client.post("/api/prefs", json={"autocal_bridge_timeout": 500})
        assert resp.status_code == 400

    def test_save_prefs_rejects_bad_bridge_poll_interval(self, client):
        resp = client.post("/api/prefs", json={"autocal_bridge_poll_interval": 20})
        assert resp.status_code == 400

    def test_run_uses_saved_prefs_as_defaults(self, client, session_id):
        client.post("/api/prefs", json={"autocal_damping": 0.42, "autocal_max_iterations": 3})
        block = threading.Event()

        def fake_measure(self, patch_obj):
            block.wait(timeout=5.0)
            raise TimeoutError("stopped")

        with patch("server._SessionCmsMeasurementSource.measure", fake_measure):
            resp = client.post(f"/api/session/{session_id}/autocal/run", json={"colours": ["Red"]})
            assert resp.json()["damping"] == 0.42
            assert resp.json()["max_iterations"] == 3
            block.set()
            _wait_until(lambda: session_id not in server_module._autocal_loops)
