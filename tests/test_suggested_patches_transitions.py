"""#638: suggested_patches must have working next/prev step transitions.

The step is reachable via jump_to_step from report (completed steps are
clickable in the Phase rail), but next_step and prev_step both raised 400
for it, dead-ending the SuggestedPatches page.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server as server_module
from server import _sessions, app


def _reset_globals():
    _sessions.clear()
    server_module._watched_session.set(None)


@pytest.fixture(autouse=True)
def clear_sessions():
    _reset_globals()
    yield
    _reset_globals()


@pytest.fixture
def client():
    return TestClient(app)


def _session_at_suggested_patches(client) -> str:
    """Create a session, complete the workflow to post_grayscale, then land on
    suggested_patches exactly as the UI does (jump_to_step from report)."""
    resp = client.post("/api/session", json={"tv_key": "u8g"})
    sid = resp.json()["id"]
    client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
    # Drive forward through each step's gate; post_measurements are empty so
    # only gates that need data would block — none do until post_grayscale.
    # Simplest reliable route: jump back from report like the Phase rail does.
    _sessions[sid]["step"] = "report"
    resp = client.post(f"/api/session/{sid}/jump", json={"step_index": 8})
    assert resp.status_code == 200, resp.text
    assert _sessions[sid]["step"] == "suggested_patches"
    return sid


class TestSuggestedPatchesStepTransitions:
    def test_next_step_goes_to_report(self, client):
        sid = _session_at_suggested_patches(client)
        resp = client.post(f"/api/session/{sid}/next")
        assert resp.status_code == 200, resp.text
        assert _sessions[sid]["step"] == "report"

    def test_prev_step_goes_to_post_grayscale(self, client):
        sid = _session_at_suggested_patches(client)
        resp = client.post(f"/api/session/{sid}/prev")
        assert resp.status_code == 200, resp.text
        assert _sessions[sid]["step"] == "post_grayscale"
