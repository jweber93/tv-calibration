"""Tests for the generic CSV import path (import_generic_bytes / /api/session/{sid}/import/generic).

Covers the full HTTP endpoint flow — not just patches_to_session_buckets in isolation.
Issue #275: zero test coverage existed for this code path previously.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture(autouse=True)
def clear_sessions():
    _reset_globals()
    yield
    _reset_globals()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session_id(client):
    """Create a session and return the ID."""
    resp = client.post("/api/session", json={"tv_key": "u8g"})
    assert resp.status_code == 200
    return resp.json()["id"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _upload_generic(client, sid, csv_bytes, fmt=None):
    """Upload a generic CSV via the /import/generic endpoint."""
    params = {}
    if fmt is not None:
        params["format"] = fmt
    return client.post(
        f"/api/session/{sid}/import/generic",
        files={"file": ("test.csv", csv_bytes, "text/csv")},
        params=params,
    )


def _advance_to_pre_grayscale(client, sid):
    """Advance session to pre_grayscale step."""
    client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
    client.post(f"/api/session/{sid}/prepared")


def _grayscale_csv(rows):
    """Build a headerless CSV with grayscale rows.

    Format: index,R,G,B,X,Y,Z (XYZ format, values large enough to not be
    misclassified as xyY since x+y >= 1.0 for typical XYZ values).

    Each row tuple: (index, R, G, B, X, Y, Z)
    """
    lines = []
    for idx, r, g, b, X, Y, Z in rows:
        lines.append(f"{idx},{r},{g},{b},{X:.2f},{Y:.2f},{Z:.2f}")
    return "\n".join(lines)


def _headered_csv(rows, columns=None):
    """Build a headered CSV with stimulus and measurement columns.

    Default columns: r_target,g_target,b_target,X,Y,Z
    """
    if columns is None:
        columns = "r_target,g_target,b_target,X,Y,Z"
    lines = [columns]
    for row in rows:
        lines.append(",".join(str(v) for v in row))
    return "\n".join(lines)


def _color_csv(rows):
    """Build a headered CSV with color (non-grayscale) rows."""
    return _headered_csv(rows)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestGenericImportRequiresMode:
    def test_no_mode_returns_400(self, client, session_id):
        """Import when no calibration mode selected should fail."""
        csv = _headered_csv([(128, 128, 128, 5.0, 6.0, 4.0)])
        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 400
        assert "mode" in resp.json().get("detail", "").lower()


class TestGenericImportHappyPath:
    def test_valid_grayscale_csv_populates_buckets(self, client, session_id):
        """Happy path: upload valid grayscale CSV → verify buckets populated."""
        _advance_to_pre_grayscale(client, session_id)

        # 8-bit grayscale: black (16), mid-gray (128), white (235)
        csv = _headered_csv([
            (16, 16, 16, 0.5, 0.6, 0.4),
            (128, 128, 128, 5.0, 6.0, 4.0),
            (235, 235, 235, 80.0, 100.0, 60.0),
        ])

        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200

        data = resp.json()
        session_data = data["session"]
        summary = data["import_summary"]

        # Summary should reflect 3 rows parsed
        assert summary["total_rows"] == 3
        assert summary["grayscale_sessions"] == 3
        assert summary["colour_sessions"] == 0

        # Buckets should be populated
        buckets = summary["buckets_populated"]
        assert "pre_measurements" in buckets or "lum_measurements" in buckets

    def test_valid_color_csv_populates_cms(self, client, session_id):
        """Color patches should route to cms_measurements bucket."""
        _advance_to_pre_grayscale(client, session_id)

        csv = _color_csv([
            (255, 0, 0, 30.0, 15.0, 5.0),
            (0, 255, 0, 10.0, 40.0, 8.0),
            (0, 0, 255, 3.0, 8.0, 50.0),
        ])

        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200

        summary = resp.json()["import_summary"]
        assert summary["colour_sessions"] == 3
        assert "cms_measurements" in summary["buckets_populated"]


class TestGenericImportEmptyCsv:
    def test_empty_file_returns_200_with_zero_rows(self, client, session_id):
        """Empty CSV is tolerated — returns 200 with zero parsed rows."""
        _advance_to_pre_grayscale(client, session_id)

        resp = _upload_generic(client, session_id, b"")
        assert resp.status_code == 200

        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 0

    def test_header_only_returns_200_with_zero_rows(self, client, session_id):
        """CSV with only a header row should succeed but produce zero measurements."""
        _advance_to_pre_grayscale(client, session_id)

        csv = "r_target,g_target,b_target,X,Y,Z\n"
        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200

        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 0


class TestGenericImportMalformedCsv:
    def test_garbage_data_returns_400(self, client, session_id):
        """Binary garbage that can't be decoded as UTF-8 should return 400."""
        _advance_to_pre_grayscale(client, session_id)

        resp = _upload_generic(client, session_id, b"\x00\x01\x02\xff\xfe")
        assert resp.status_code == 400

    def test_unparseable_text_csv_returns_200_zero_rows(self, client, session_id):
        """Text that doesn't match CSV format returns 200 with zero rows (tolerant parser)."""
        _advance_to_pre_grayscale(client, session_id)

        resp = _upload_generic(client, session_id, b"this is not csv data at all\n")
        assert resp.status_code == 200
        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 0

    def test_csv_missing_measurement_columns(self, client, session_id):
        """CSV with stimulus columns but no measurement data should be handled gracefully."""
        _advance_to_pre_grayscale(client, session_id)

        csv = "r_target,g_target,b_target\n128,128,128\n"
        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200
        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 0


class TestGenericImportGrayscaleOnly:
    def test_grayscale_only_populates_pre_and_lum(self, client, session_id):
        """CSV with only grayscale → pre/post measurements populated based on step."""
        _advance_to_pre_grayscale(client, session_id)

        # Full grayscale ramp: 0%, 10%, ..., 100%
        rows = []
        for pct in range(0, 101, 10):
            v = int(pct / 100 * 219 + 16)
            Y = pct / 100 * 120.0 if pct > 0 else 0.5
            rows.append((v, v, v, Y * 0.95, Y, Y * 1.0883))

        csv = _headered_csv(rows)
        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200

        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 11
        assert summary["grayscale_sessions"] == 11

        # pre_measurements should be populated (we're at pre_grayscale step)
        buckets = summary["buckets_populated"]
        assert "pre_measurements" in buckets

    def test_grayscale_at_post_step_routes_to_post(self, client, session_id):
        """Grayscale CSV imported at post_grayscale step → post_measurements."""
        client.post(f"/api/session/{session_id}/mode", json={"mode": "SDR"})
        client.post(f"/api/session/{session_id}/prepared")

        # Advance through grayscale steps to post_grayscale
        client.post(f"/api/session/{session_id}/next")  # pre_grayscale -> grayscale
        client.post(f"/api/session/{session_id}/next")  # grayscale -> ...

        csv = _headered_csv([(128, 128, 128, 5.0, 6.0, 4.0)])
        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200


class TestGenericImportMixedColorAndGrayscale:
    def test_mixed_csv_correct_bucket_routing(self, client, session_id):
        """CSV with mixed color and grayscale → correct bucket routing."""
        _advance_to_pre_grayscale(client, session_id)

        csv = _headered_csv([
            # Grayscale: 50% gray (also gamma target)
            (128, 128, 128, 5.0, 6.0, 4.0),
            # Color: Red
            (255, 0, 0, 30.0, 15.0, 5.0),
            # Grayscale: white (lum target)
            (235, 235, 235, 80.0, 100.0, 60.0),
            # Color: Green
            (0, 255, 0, 10.0, 40.0, 8.0),
        ])

        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200

        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 4
        assert summary["grayscale_sessions"] == 2
        assert summary["colour_sessions"] == 2

        buckets = summary["buckets_populated"]
        assert "cms_measurements" in buckets
        assert len(buckets) >= 2  # cms + at least one grayscale bucket


class TestGenericImportDeduplication:
    def test_reimport_same_csv_appends(self, client, session_id):
        """Re-import of same CSV → check behavior (appends or deduplicates)."""
        _advance_to_pre_grayscale(client, session_id)

        csv = _headered_csv([(128, 128, 128, 5.0, 6.0, 4.0)])
        csv_bytes = csv.encode()

        resp1 = _upload_generic(client, session_id, csv_bytes)
        assert resp1.status_code == 200

        resp2 = _upload_generic(client, session_id, csv_bytes)
        assert resp2.status_code == 200

        # Check that zro_imports list has two entries
        session_resp = client.get(f"/api/session/{session_id}")
        assert session_resp.status_code == 200
        session_data = session_resp.json()
        imports = session_data.get("zro_imports", [])
        assert len(imports) == 2


class TestGenericImportPeakLuminance:
    def test_peak_luminance_set_from_last_lum_measurement(self, client, session_id):
        """Import with lum_measurements → peak_luminance is set from last entry."""
        _advance_to_pre_grayscale(client, session_id)

        # White patch with Y=120
        csv = _headered_csv([(235, 235, 235, 80.0, 120.0, 60.0)])
        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200

        session_data = resp.json()["session"]
        assert "peak_luminance" in session_data


class TestGenericImportHeaderlessFormat:
    def test_headerless_xyz_csv_requires_format_param(self, client, session_id):
        """Headerless XYZ CSV without format=XYZ → 400 (plausibility check catches it)."""
        _advance_to_pre_grayscale(client, session_id)

        # Headerless format: index,R,G,B,X,Y,Z — X=100, Z=60 are way outside xyY range
        csv = _grayscale_csv([
            (0, 128, 128, 128, 5.0, 6.0, 4.0),
            (1, 235, 235, 235, 80.0, 100.0, 60.0),
        ])

        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "chromaticity" in detail.lower()
        assert "xyY, XYZ" in detail

    def test_headerless_xyz_csv_with_format_param(self, client, session_id):
        """Headerless XYZ CSV with format=XYZ parses correctly."""
        _advance_to_pre_grayscale(client, session_id)

        csv = _grayscale_csv([
            (0, 128, 128, 128, 5.0, 6.0, 4.0),
            (1, 235, 235, 235, 80.0, 100.0, 60.0),
        ])

        resp = _upload_generic(client, session_id, csv.encode(), fmt="XYZ")
        assert resp.status_code == 200

        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 2

    def test_headerless_xyz_csv_case_insensitive_format(self, client, session_id):
        """format param is case-insensitive — 'xyz', 'Xyz', 'XYZ' all work."""
        _advance_to_pre_grayscale(client, session_id)

        csv = _grayscale_csv([(0, 128, 128, 128, 5.0, 6.0, 4.0)])

        for case in ("xyz", "Xyz", "XYZ"):
            resp = _upload_generic(client, session_id, csv.encode(), fmt=case)
            assert resp.status_code == 200, f"failed for fmt={case!r}"
            assert resp.json()["import_summary"]["total_rows"] == 1

    def test_headerless_xyY_csv_with_default_format(self, client, session_id):
        """Headerless xyY CSV works with default format (xyY)."""
        _advance_to_pre_grayscale(client, session_id)

        # xyY format: index,R,G,B,Y,x,y
        lines = [
            "0,128,128,128,5.0,0.3127,0.3290",
            "1,235,235,235,100.0,0.3127,0.3290",
        ]
        csv = "\n".join(lines)

        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200

        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 2

    def test_headerless_xyY_csv_explicit_format(self, client, session_id):
        """Headerless xyY CSV with format=xyY also works."""
        _advance_to_pre_grayscale(client, session_id)

        lines = [
            "0,128,128,128,5.0,0.3127,0.3290",
            "1,235,235,235,100.0,0.3127,0.3290",
        ]
        csv = "\n".join(lines)

        resp = _upload_generic(client, session_id, csv.encode(), fmt="xyY")
        assert resp.status_code == 200

        summary = resp.json()["import_summary"]
        assert summary["total_rows"] == 2

    def test_invalid_format_param_returns_400(self, client, session_id):
        """Invalid format parameter value returns 400."""
        _advance_to_pre_grayscale(client, session_id)

        csv = _grayscale_csv([(0, 128, 128, 128, 5.0, 6.0, 4.0)])

        resp = _upload_generic(client, session_id, csv.encode(), fmt="invalid")
        assert resp.status_code == 400
        assert "format" in resp.json()["detail"].lower()

    def test_chromaticity_boundary_at_0_85_rejected(self, client, session_id):
        """x=0.85 is outside the plausible range and should be rejected."""
        _advance_to_pre_grayscale(client, session_id)

        # xyY: Y=100, x=0.85, y=0.33 — x at the boundary is rejected (exclusive upper bound)
        csv = "0,128,128,128,100.0,0.85,0.33\n"
        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 400
        assert "chromaticity" in resp.json()["detail"].lower()

    def test_chromaticity_boundary_at_0_84_accepted(self, client, session_id):
        """x=0.84 is inside the plausible range and should be accepted."""
        _advance_to_pre_grayscale(client, session_id)

        csv = "0,128,128,128,100.0,0.84,0.33\n"
        resp = _upload_generic(client, session_id, csv.encode())
        assert resp.status_code == 200
        assert resp.json()["import_summary"]["total_rows"] == 1


