"""
Test coverage for next_step luminance threshold validation (issue #276).

The luminance step in next_step() validates that the measured peak
luminance is within a configurable percentage of the target.  This
module covers the rejection path that was previously untested.
"""
import io

from fastapi.testclient import TestClient

import pytest

from calibrator import Measurement
from server import app, _sessions


@pytest.fixture(scope="session")
def client():
    """Alias for conftest's api_client — local to this test file."""
    return TestClient(app)


def _create_session(client: TestClient) -> str:
    """Create a session, set SDR mode, and prepare it."""
    resp = client.post("/api/session", json={"tv_key": "u8g", "simulate": True})
    assert resp.status_code == 200
    sid = resp.json()["id"]
    client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
    client.post(f"/api/session/{sid}/prepared")
    return sid


def _upload_grayscale(client: TestClient, sid: str):
    """Upload a full 11-point grayscale ramp so pre_grayscale is complete."""
    csv = b"""Date and time\t R\t G\t B\t Y\t x\t y\t msec
15/03/2026 11:00:00\t16\t16\t16\t0.01\t0.313\t0.329\t 360ms
15/03/2026 11:00:03\t26\t26\t26\t0.5\t0.313\t0.329\t 360ms
15/03/2026 11:00:06\t51\t51\t51\t2.0\t0.313\t0.329\t 360ms
15/03/2026 11:00:09\t77\t77\t77\t5.5\t0.313\t0.329\t 360ms
15/03/2026 11:00:12\t102\t102\t102\t10.0\t0.313\t0.329\t 360ms
15/03/2026 11:00:15\t128\t128\t128\t18.0\t0.313\t0.329\t 360ms
15/03/2026 11:00:18\t153\t153\t153\t27.0\t0.313\t0.329\t 360ms
15/03/2026 11:00:21\t179\t179\t179\t38.0\t0.313\t0.329\t 360ms
15/03/2026 11:00:24\t204\t204\t204\t48.0\t0.313\t0.329\t 360ms
15/03/2026 11:00:27\t230\t230\t230\t56.0\t0.313\t0.329\t 360ms
15/03/2026 11:00:30\t235\t235\t235\t60.0\t0.313\t0.329\t 360ms"""
    resp = client.post(
        f"/api/session/{sid}/import/zro",
        files={"file": ("grayscale.csv", io.BytesIO(csv), "text/csv")},
    )
    assert resp.status_code == 200


def _advance_to_luminance(client: TestClient, sid: str):
    """Upload grayscale and advance from pre_grayscale to luminance step."""
    _upload_grayscale(client, sid)
    resp = client.post(f"/api/session/{sid}/next")
    assert resp.status_code == 200
    assert resp.json()["step"] == "luminance"


def _add_lum_measurement(client: TestClient, sid: str, y_value: float):
    """Inject a luminance measurement directly into the session store."""
    _sessions[sid]["lum_measurements"] = [
        Measurement(
            x=0.313, y=0.329, Y=y_value, stimulus_rgb=(255, 255, 255),
        )
    ]


class TestNextStepLuminanceThreshold:

    def test_within_threshold_advances_to_white_balance(self, client):
        """Luminance within 5% of SDR target (120 nits) → step advances."""
        sid = _create_session(client)
        _advance_to_luminance(client, sid)

        # 115 nits is ~4.2% off 120 — within the default 5% threshold
        _add_lum_measurement(client, sid, y_value=115.0)

        resp = client.post(f"/api/session/{sid}/next")
        assert resp.status_code == 200
        assert resp.json()["step"] == "white_balance"

    def test_above_threshold_returns_400(self, client):
        """Luminance significantly above target → 400 with descriptive error."""
        sid = _create_session(client)
        _advance_to_luminance(client, sid)

        # 140 nits is ~16.7% off 120 — well outside the 5% threshold
        _add_lum_measurement(client, sid, y_value=140.0)

        resp = client.post(f"/api/session/{sid}/next")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "140.0" in detail or "140" in detail
        assert "Adjust Backlight" in detail

    def test_below_threshold_returns_400(self, client):
        """Luminance significantly below target → 400 with descriptive error."""
        sid = _create_session(client)
        _advance_to_luminance(client, sid)

        # 90 nits is ~25% off 120 — well outside the 5% threshold
        _add_lum_measurement(client, sid, y_value=90.0)

        resp = client.post(f"/api/session/{sid}/next")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "90.0" in detail or "90" in detail
        assert "Adjust Backlight" in detail

    def test_no_luminance_measurement_returns_400(self, client):
        """Calling next with no luminance readings → 400."""
        sid = _create_session(client)
        _advance_to_luminance(client, sid)

        # Don't add any luminance measurement
        resp = client.post(f"/api/session/{sid}/next")
        assert resp.status_code == 400
        assert "luminance reading" in resp.json()["detail"].lower()
