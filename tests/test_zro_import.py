"""
Tests for calibrator/zro_import.py (unit) and the
POST /api/session/{sid}/import/zro endpoint (integration).
"""
import io
import pytest
from fastapi.testclient import TestClient

from calibrator import Measurement
from calibrator.zro_import import (
    ZROImportResult,
    _classify,
    _rgb_to_stimulus_pct,
    _check_abl,
    _group_into_sessions,
    _Row,
    parse_zro_csv,
    merge_into_session,
    SESSION_BREAK_SECONDS,
)
from server import app, _sessions

# ── Shared test fixtures ──────────────────────────────────────────────────────

# The exact 18-row export from the user's real ZRO session (colours + grayscale)
FULL_CSV = b"""Date and time\t R\t G\t B\t Y\t x\t y\t msec
15/03/2026 10:46:10\t235\t235\t235\t36.726107\t0.3079\t0.3265\t 363ms
15/03/2026 10:46:13\t235\t16\t16\t32.578549\t0.6249\t0.3299\t 863ms
15/03/2026 10:46:15\t16\t235\t16\t88.505467\t0.2955\t0.5931\t 363ms
15/03/2026 10:46:17\t16\t16\t235\t10.228269\t0.1629\t0.062\t 361ms
15/03/2026 10:46:20\t235\t235\t16\t105.030201\t0.4153\t0.5029\t 362ms
15/03/2026 10:46:22\t16\t235\t235\t105.381852\t0.2233\t0.3245\t 360ms
15/03/2026 10:46:24\t235\t16\t235\t36.309427\t0.3144\t0.1499\t 362ms
15/03/2026 10:48:14\t16\t16\t16\t0.313596\t0.3088\t0.3182\t 862ms
15/03/2026 10:48:17\t26\t26\t26\t0.940789\t0.3088\t0.3182\t 865ms
15/03/2026 10:48:20\t51\t51\t51\t4.488502\t0.3117\t0.3285\t 863ms
15/03/2026 10:48:23\t77\t77\t77\t11.566695\t0.3138\t0.3293\t 865ms
15/03/2026 10:48:25\t102\t102\t102\t17.581601\t0.3123\t0.3264\t 360ms
15/03/2026 10:48:27\t128\t128\t128\t21.62418\t0.3135\t0.3282\t 360ms
15/03/2026 10:48:30\t153\t153\t153\t45.44085\t0.3138\t0.3292\t 360ms
15/03/2026 10:48:32\t179\t179\t179\t63.074181\t0.3078\t0.3251\t 363ms
15/03/2026 10:48:34\t204\t204\t204\t29.089545\t0.3134\t0.3304\t 364ms
15/03/2026 10:48:37\t230\t230\t230\t108.728095\t0.3077\t0.3253\t 361ms
15/03/2026 10:48:39\t235\t235\t235\t85.092501\t0.3074\t0.3267\t 363ms"""

# Grayscale-only (monotonic, no ABL) — clean 11-point ramp at 60 nit peak
CLEAN_GRAYSCALE_CSV = b"""Date and time\t R\t G\t B\t Y\t x\t y\t msec
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

# Two grayscale passes back-to-back (second one 2 minutes later)
TWO_GRAYSCALE_CSV = (
    CLEAN_GRAYSCALE_CSV
    + b"\n"
    + b"""15/03/2026 11:03:00\t16\t16\t16\t0.01\t0.313\t0.329\t 360ms
15/03/2026 11:03:03\t26\t26\t26\t0.6\t0.313\t0.329\t 360ms
15/03/2026 11:03:06\t51\t51\t51\t2.2\t0.313\t0.329\t 360ms
15/03/2026 11:03:09\t77\t77\t77\t5.8\t0.313\t0.329\t 360ms
15/03/2026 11:03:12\t102\t102\t102\t10.5\t0.313\t0.329\t 360ms
15/03/2026 11:03:15\t128\t128\t128\t19.0\t0.313\t0.329\t 360ms
15/03/2026 11:03:18\t153\t153\t153\t28.0\t0.313\t0.329\t 360ms
15/03/2026 11:03:21\t179\t179\t179\t39.0\t0.313\t0.329\t 360ms
15/03/2026 11:03:24\t204\t204\t204\t49.0\t0.313\t0.329\t 360ms
15/03/2026 11:03:27\t230\t230\t230\t57.0\t0.313\t0.329\t 360ms
15/03/2026 11:03:30\t235\t235\t235\t61.0\t0.313\t0.329\t 360ms"""
)


# ── Unit tests: _classify ─────────────────────────────────────────────────────

class TestClassify:
    def test_white(self):
        assert _classify(235, 235, 235) == "white"

    def test_black(self):
        assert _classify(16, 16, 16) == "black"

    def test_grayscale_mid(self):
        assert _classify(128, 128, 128) == "grayscale"

    def test_grayscale_near_equal_channels(self):
        # Within GRAYSCALE_EQUAL_TOLERANCE (4)
        assert _classify(100, 102, 99) == "grayscale"

    def test_red_primary(self):
        assert _classify(235, 16, 16) == "red"

    def test_green_primary(self):
        assert _classify(16, 235, 16) == "green"

    def test_blue_primary(self):
        assert _classify(16, 16, 235) == "blue"

    def test_yellow_secondary(self):
        assert _classify(235, 235, 16) == "yellow"

    def test_cyan_secondary(self):
        assert _classify(16, 235, 235) == "cyan"

    def test_magenta_secondary(self):
        assert _classify(235, 16, 235) == "magenta"

    def test_unknown_mixed(self):
        assert _classify(200, 100, 50) == "unknown"


# ── Unit tests: _rgb_to_stimulus_pct ─────────────────────────────────────────

class TestRgbToStimulusPct:
    def test_black_floor(self):
        assert _rgb_to_stimulus_pct(16) == 0.0

    def test_white_ceiling(self):
        assert _rgb_to_stimulus_pct(235) == 100.0

    def test_below_floor_clamps(self):
        assert _rgb_to_stimulus_pct(0) == 0.0

    def test_10pct_step(self):
        # ZRO uses val=26 for 10% (26/255 * 100 ≈ 10.2)
        pct = _rgb_to_stimulus_pct(26)
        assert 9.0 <= pct <= 11.5

    def test_legal_range_10pct_step(self):
        pct = _rgb_to_stimulus_pct(38)
        assert 9.0 <= pct <= 11.5

    def test_legal_range_20pct_step(self):
        pct = _rgb_to_stimulus_pct(60)
        assert 19.0 <= pct <= 21.5

    def test_50pct_step(self):
        pct = _rgb_to_stimulus_pct(128)
        assert 49.0 <= pct <= 51.5

    def test_90pct_step(self):
        pct = _rgb_to_stimulus_pct(230)
        assert 88.0 <= pct <= 92.0


# ── Unit tests: _check_abl ───────────────────────────────────────────────────

class TestCheckAbl:
    def _make_rows(self, rgb_y_pairs):
        from datetime import datetime
        rows = []
        for i, (rgb, Y) in enumerate(rgb_y_pairs):
            rows.append(_Row(
                dt=datetime(2026, 3, 15, 10, 48, i * 3),
                r=rgb, g=rgb, b=rgb,
                Y=Y, x=0.313, y=0.329,
            ))
        return rows

    def test_clean_ramp_no_warnings(self):
        rows = self._make_rows([
            (16, 0.01), (26, 0.5), (51, 2.0), (77, 5.5),
            (102, 10.0), (128, 18.0), (153, 27.0), (179, 38.0),
            (204, 48.0), (230, 56.0), (235, 60.0),
        ])
        warnings = _check_abl(rows)
        assert warnings == []

    def test_abl_at_80pct(self):
        # Luminance drops at 80% (204) — exactly your real data
        rows = self._make_rows([
            (16, 0.3), (26, 0.9), (51, 4.5), (77, 11.6),
            (102, 17.6), (128, 21.6), (153, 45.4), (179, 63.1),
            (204, 29.1),   # ← ABL drop
            (230, 108.7), (235, 85.1),  # ← also non-monotonic
        ])
        warnings = _check_abl(rows)
        assert len(warnings) >= 1
        assert any("80%" in w or "29" in w for w in warnings)

    def test_abl_at_100pct(self):
        # Peak white lower than near-white
        rows = self._make_rows([
            (16, 0.3), (128, 30.0), (230, 110.0), (235, 85.0),
        ])
        warnings = _check_abl(rows)
        assert len(warnings) >= 1
        assert any("100%" in w for w in warnings)

    def test_small_drop_within_tolerance_no_warning(self):
        # A 4% drop is within the 5% tolerance — should not warn
        rows = self._make_rows([(100, 20.0), (150, 19.5)])
        warnings = _check_abl(rows)
        assert warnings == []

    def test_too_few_rows_no_warnings(self):
        rows = self._make_rows([(100, 10.0)])
        assert _check_abl(rows) == []


# ── Unit tests: _group_into_sessions ────────────────────────────────────────

class TestGroupIntoSessions:
    def _row(self, dt_str):
        from datetime import datetime
        dt = datetime.fromisoformat(dt_str)
        return _Row(dt=dt, r=128, g=128, b=128, Y=20.0, x=0.313, y=0.329)

    def test_single_group_no_gap(self):
        rows = [
            self._row("2026-03-15T10:46:10"),
            self._row("2026-03-15T10:46:13"),
            self._row("2026-03-15T10:46:15"),
        ]
        groups = _group_into_sessions(rows)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_gap_splits_into_two_groups(self):
        rows = [
            self._row("2026-03-15T10:46:10"),
            self._row("2026-03-15T10:46:24"),
            # > SESSION_BREAK_SECONDS gap
            self._row(f"2026-03-15T10:48:14"),
            self._row("2026-03-15T10:48:17"),
        ]
        groups = _group_into_sessions(rows)
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 2

    def test_empty_returns_empty(self):
        assert _group_into_sessions([]) == []

    def test_real_csv_gap(self):
        # The actual gap between colour pass (10:46:24) and grayscale (10:48:14)
        # is 110 seconds — well above the 45s threshold.
        rows = [
            self._row("2026-03-15T10:46:10"),
            self._row("2026-03-15T10:46:24"),
            self._row("2026-03-15T10:48:14"),   # 110s gap
            self._row("2026-03-15T10:48:17"),
        ]
        groups = _group_into_sessions(rows)
        assert len(groups) == 2


# ── Unit tests: parse_zro_csv (full integration of parser logic) ──────────────

class TestParseZroCsv:

    def test_full_csv_row_count(self):
        r = parse_zro_csv(FULL_CSV)
        assert r.total_rows == 18

    def test_full_csv_no_unknown_rows(self):
        r = parse_zro_csv(FULL_CSV)
        assert r.unknown_rows == 0

    def test_full_csv_two_sessions(self):
        r = parse_zro_csv(FULL_CSV)
        assert r.sessions_detected == 2

    def test_full_csv_one_colour_session(self):
        r = parse_zro_csv(FULL_CSV)
        assert r.colour_sessions == 1

    def test_full_csv_one_grayscale_session(self):
        r = parse_zro_csv(FULL_CSV)
        assert r.grayscale_sessions == 1

    def test_session_break_timestamp_recorded(self):
        r = parse_zro_csv(FULL_CSV)
        assert len(r.session_breaks) == 1
        # Break is at the start of the grayscale pass
        assert "10:48" in r.session_breaks[0]

    # ── Bucket routing ──────────────────────────────────────────────────────

    def test_cms_bucket_has_six_patches(self):
        r = parse_zro_csv(FULL_CSV)
        assert len(r.cms_measurements) == 6

    def test_cms_bucket_labels(self):
        r = parse_zro_csv(FULL_CSV)
        labels = {m["label"] for m in r.cms_measurements}
        assert labels == {"Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"}

    def test_pre_measurements_has_eleven_steps(self):
        r = parse_zro_csv(FULL_CSV)
        assert len(r.pre_measurements) == 11

    def test_pre_measurements_covers_full_range(self):
        r = parse_zro_csv(FULL_CSV)
        pcts = {m["label"] for m in r.pre_measurements}
        assert "Black (0%)" in pcts
        assert "White (100%)" in pcts

    def test_lum_measurements_contains_white(self):
        r = parse_zro_csv(FULL_CSV)
        # White from colours pass + white from grayscale pass
        assert len(r.lum_measurements) >= 1
        assert all(m["label"].startswith("White") for m in r.lum_measurements)

    def test_wb_measurements_has_gain_and_offset(self):
        r = parse_zro_csv(FULL_CSV)
        assert len(r.wb_measurements) == 2
        labels = " ".join(m["label"] for m in r.wb_measurements)
        assert "Gain" in labels or "80%" in labels
        assert "Offset" in labels or "30%" in labels

    def test_gamma_measurements_captures_full_five_percent_grid(self):
        r = parse_zro_csv(FULL_CSV)
        labels = [m["label"] for m in r.gamma_measurements]
        assert len(r.gamma_measurements) == 11
        assert all(label.startswith("Gamma ") for label in labels)
        snapped_pcts = [int(label.split(" ", 1)[1].rstrip("%")) for label in labels]
        assert all(pct % 5 == 0 for pct in snapped_pcts)
        assert min(snapped_pcts) >= 5
        assert max(snapped_pcts) <= 95

    def test_gamma_measurements_use_snapped_nominal_labels(self):
        r = parse_zro_csv(FULL_CSV)
        labels = {m["label"] for m in r.gamma_measurements}
        assert "Gamma 5%" in labels
        assert "Gamma 50%" in labels
        assert "Gamma 95%" in labels

    # ── Measurement object fields ───────────────────────────────────────────

    def test_measurement_dicts_have_required_keys(self):
        r = parse_zro_csv(FULL_CSV)
        required = {"x", "y", "Y", "X", "Z", "timestamp", "label", "stimulus_rgb"}
        for m in r.cms_measurements + r.pre_measurements:
            assert required.issubset(m.keys()), f"Missing keys in {m}"

    def test_xyz_derived_from_xyl(self):
        r = parse_zro_csv(FULL_CSV)
        # X and Z should be non-zero for coloured patches
        red = next(m for m in r.cms_measurements if m["label"] == "Red")
        assert red["X"] > 0
        assert red["Z"] >= 0  # Z is small for red

    def test_stimulus_rgb_stored_as_list(self):
        r = parse_zro_csv(FULL_CSV)
        for m in r.pre_measurements:
            assert isinstance(m["stimulus_rgb"], (list, tuple))
            assert len(m["stimulus_rgb"]) == 3

    def test_timestamp_iso_format(self):
        from datetime import datetime
        r = parse_zro_csv(FULL_CSV)
        for m in r.pre_measurements:
            # Should be parseable as ISO 8601
            dt = datetime.fromisoformat(m["timestamp"])
            assert dt.year == 2026

    # ── ABL detection ───────────────────────────────────────────────────────

    def test_abl_warnings_detected_in_real_data(self):
        r = parse_zro_csv(FULL_CSV)
        assert len(r.abl_warnings) == 2

    def test_abl_warning_mentions_80pct(self):
        r = parse_zro_csv(FULL_CSV)
        combined = " ".join(r.abl_warnings)
        assert "80%" in combined

    def test_abl_warning_mentions_100pct(self):
        r = parse_zro_csv(FULL_CSV)
        combined = " ".join(r.abl_warnings)
        assert "100%" in combined

    def test_clean_grayscale_no_abl_warnings(self):
        r = parse_zro_csv(CLEAN_GRAYSCALE_CSV)
        assert r.abl_warnings == []

    # ── Two grayscale passes ────────────────────────────────────────────────

    def test_two_grayscale_passes_split_correctly(self):
        r = parse_zro_csv(TWO_GRAYSCALE_CSV)
        assert r.grayscale_sessions == 2

    def test_first_pass_goes_to_pre(self):
        r = parse_zro_csv(TWO_GRAYSCALE_CSV)
        assert len(r.pre_measurements) == 11

    def test_second_pass_goes_to_post(self):
        r = parse_zro_csv(TWO_GRAYSCALE_CSV)
        assert len(r.post_measurements) == 11

    # ── Input format variants ───────────────────────────────────────────────

    def test_accepts_bytes(self):
        r = parse_zro_csv(FULL_CSV)
        assert r.total_rows == 18

    def test_accepts_string(self):
        r = parse_zro_csv(FULL_CSV.decode())
        assert r.total_rows == 18

    def test_accepts_file_like(self):
        r = parse_zro_csv(io.BytesIO(FULL_CSV))
        assert r.total_rows == 18

    def test_accepts_utf8_bom(self):
        bom_csv = b"\xef\xbb\xbf" + FULL_CSV
        r = parse_zro_csv(bom_csv)
        assert r.total_rows == 18

    def test_comma_separated_fallback(self):
        # Build a minimal comma-separated version of one row
        csv_comma = (
            b"Date and time, R, G, B, Y, x, y, msec\n"
            b"15/03/2026 10:46:10,235,16,16,32.578549,0.6249,0.3299, 363ms\n"
        )
        r = parse_zro_csv(csv_comma)
        assert r.total_rows == 1
        assert len(r.cms_measurements) == 1
        assert r.cms_measurements[0]["label"] == "Red"

    # ── Malformed input ─────────────────────────────────────────────────────

    def test_empty_bytes_returns_zero_rows(self):
        r = parse_zro_csv(b"")
        assert r.total_rows == 0

    def test_header_only_returns_zero_rows(self):
        r = parse_zro_csv(b"Date and time\t R\t G\t B\t Y\t x\t y\t msec\n")
        assert r.total_rows == 0

    def test_bad_numeric_value_skipped(self):
        bad = (
            b"Date and time\t R\t G\t B\t Y\t x\t y\t msec\n"
            b"15/03/2026 10:46:10\tNaN\t16\t16\t32.5\t0.62\t0.33\t 360ms\n"
            b"15/03/2026 10:46:13\t235\t16\t16\t32.5\t0.62\t0.33\t 360ms\n"
        )
        r = parse_zro_csv(bad)
        # Good row parsed, bad NaN row rejected and counted in unknown_rows
        assert r.total_rows == 1
        assert r.unknown_rows == 1        # the NaN row was correctly rejected
        assert len(r.cms_measurements) == 1  # the good Red patch made it through

    def test_wrong_columns_returns_zero_rows(self):
        garbage = b"foo,bar,baz\n1,2,3\n"
        r = parse_zro_csv(garbage)
        assert r.total_rows == 0

    def test_subsecond_timestamps(self):
        """Test that sub-second timestamps are parsed correctly."""
        # Read the test fixture file
        with open("tests/fixtures/zro_subsecond.csv", "r") as f:
            csv_content = f.read()
        
        # Parse the CSV - should not fail and should parse all rows
        result = parse_zro_csv(csv_content)
        
        # Should have parsed 4 rows successfully
        assert result.total_rows == 4
        assert result.unknown_rows == 0
        
        # Should have parsed all rows with timestamps including subseconds
        assert len(result.pre_measurements) == 4
        
        # Verify the first timestamp was parsed correctly (has subsecond precision)
        first_timestamp = result.pre_measurements[0]["timestamp"]
        assert "19:12:34.527" in first_timestamp or "19:12:34.527000" in first_timestamp

    # ── merge_into_session helper ───────────────────────────────────────────

    def test_merge_into_session_populates_buckets(self):
        r = parse_zro_csv(FULL_CSV)
        session = {
            "pre_measurements": [], "post_measurements": [],
            "cms_measurements": [], "wb_measurements": [],
            "lum_measurements": [], "gamma_measurements": [],
        }
        merge_into_session(session, r)
        assert len(session["pre_measurements"]) == 11
        assert len(session["cms_measurements"]) == 6

    def test_merge_appends_not_replaces(self):
        r = parse_zro_csv(CLEAN_GRAYSCALE_CSV)
        existing = {"x": 0.313, "y": 0.329, "Y": 50.0, "X": 0.0, "Z": 0.0,
                    "timestamp": "", "label": "existing", "stimulus_rgb": [128, 128, 128]}
        session = {
            "pre_measurements": [existing],
            "post_measurements": [], "cms_measurements": [],
            "wb_measurements": [], "lum_measurements": [], "gamma_measurements": [],
        }
        merge_into_session(session, r)
        assert len(session["pre_measurements"]) == 12  # 1 existing + 11 imported

    def test_merge_records_zro_imports_metadata(self):
        r = parse_zro_csv(FULL_CSV)
        session = {
            "pre_measurements": [], "post_measurements": [],
            "cms_measurements": [], "wb_measurements": [],
            "lum_measurements": [], "gamma_measurements": [],
        }
        merge_into_session(session, r)
        assert "zro_imports" in session
        assert len(session["zro_imports"]) == 1
        meta = session["zro_imports"][0]
        assert meta["total_rows"] == 18
        assert "buckets_populated" in meta


# ── API endpoint tests ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_sessions():
    _sessions.clear()
    yield
    _sessions.clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session_id(client):
    """Create a simulated session with SDR mode selected, ready for pre_grayscale."""
    resp = client.post("/api/session", json={"tv_key": "u8g", "simulate": True})
    assert resp.status_code == 200
    sid = resp.json()["id"]
    client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
    client.post(f"/api/session/{sid}/prepared")
    return sid


def _upload(client, sid, csv_bytes, filename="measurements.csv"):
    return client.post(
        f"/api/session/{sid}/import/zro",
        files={"file": (filename, io.BytesIO(csv_bytes), "text/csv")},
    )


# Cumulative log: 11 grayscale rows followed immediately (< 45 s) by 6 colour
# rows — all in one session group.  Grayscale dominates so the group is
# classified as "grayscale", but colour rows must still reach cms_measurements.
_MIXED_GROUP_CSV = b"""Date and time\tR\tG\tB\tY\tx\ty\tmsec
15/03/2026 12:00:00\t16\t16\t16\t0.01\t0.313\t0.329\t360ms
15/03/2026 12:00:03\t26\t26\t26\t0.5\t0.313\t0.329\t360ms
15/03/2026 12:00:06\t51\t51\t51\t2.0\t0.313\t0.329\t360ms
15/03/2026 12:00:09\t77\t77\t77\t5.5\t0.313\t0.329\t360ms
15/03/2026 12:00:12\t102\t102\t102\t10.0\t0.313\t0.329\t360ms
15/03/2026 12:00:15\t128\t128\t128\t18.0\t0.313\t0.329\t360ms
15/03/2026 12:00:18\t153\t153\t153\t27.0\t0.313\t0.329\t360ms
15/03/2026 12:00:21\t179\t179\t179\t38.0\t0.313\t0.329\t360ms
15/03/2026 12:00:24\t204\t204\t204\t48.0\t0.313\t0.329\t360ms
15/03/2026 12:00:27\t230\t230\t230\t56.0\t0.313\t0.329\t360ms
15/03/2026 12:00:30\t235\t235\t235\t60.0\t0.313\t0.329\t360ms
15/03/2026 12:00:33\t235\t16\t16\t32.0\t0.640\t0.330\t360ms
15/03/2026 12:00:36\t16\t235\t16\t88.0\t0.300\t0.600\t360ms
15/03/2026 12:00:39\t16\t16\t235\t10.0\t0.150\t0.060\t360ms
15/03/2026 12:00:42\t235\t235\t16\t105.0\t0.420\t0.505\t360ms
15/03/2026 12:00:45\t16\t235\t235\t105.0\t0.225\t0.329\t360ms
15/03/2026 12:00:48\t235\t16\t235\t36.0\t0.321\t0.154\t360ms"""


class TestMixedGroupColourExtraction:
    """Colour rows mixed into a grayscale-dominated session group must reach cms_measurements."""

    def test_cms_measurements_extracted_from_mixed_group(self):
        r = parse_zro_csv(_MIXED_GROUP_CSV)
        assert len(r.cms_measurements) == 6

    def test_cms_labels_correct_in_mixed_group(self):
        r = parse_zro_csv(_MIXED_GROUP_CSV)
        labels = {m["label"] for m in r.cms_measurements}
        assert labels == {"Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"}

    def test_grayscale_still_parsed_in_mixed_group(self):
        r = parse_zro_csv(_MIXED_GROUP_CSV)
        assert len(r.pre_measurements) == 11


class TestImportZroEndpoint:

    def test_valid_import_returns_200(self, client, session_id):
        resp = _upload(client, session_id, FULL_CSV)
        assert resp.status_code == 200

    def test_response_contains_session_and_summary(self, client, session_id):
        resp = _upload(client, session_id, FULL_CSV)
        data = resp.json()
        assert "session" in data
        assert "import_summary" in data

    def test_summary_row_count(self, client, session_id):
        resp = _upload(client, session_id, FULL_CSV)
        assert resp.json()["import_summary"]["total_rows"] == 18

    def test_summary_bucket_counts(self, client, session_id):
        resp = _upload(client, session_id, FULL_CSV)
        buckets = resp.json()["import_summary"]["buckets_populated"]
        assert buckets.get("pre_measurements") == 11
        assert buckets.get("cms_measurements") is None

    def test_abl_warnings_in_summary(self, client, session_id):
        resp = _upload(client, session_id, FULL_CSV)
        warnings = resp.json()["import_summary"]["abl_warnings"]
        assert len(warnings) == 2

    def test_session_step_stays_on_pre_grayscale_after_full_ramp_until_continue(self, client, session_id):
        resp = _upload(client, session_id, FULL_CSV)
        assert resp.json()["session"]["step"] == "pre_grayscale"

    def test_session_step_stays_at_pre_grayscale_with_partial_ramp(self, client, session_id):
        # Only 3 rows (colours) — not a full ramp, should stay at pre_grayscale
        colours_only = b"""Date and time\t R\t G\t B\t Y\t x\t y\t msec
15/03/2026 10:46:10\t235\t16\t16\t32.5\t0.6249\t0.3299\t 863ms
15/03/2026 10:46:13\t16\t235\t16\t88.5\t0.2955\t0.5931\t 363ms
15/03/2026 10:46:15\t16\t16\t235\t10.2\t0.1629\t0.062\t 361ms"""
        resp = _upload(client, session_id, colours_only)
        assert resp.json()["session"]["step"] == "pre_grayscale"

    def test_peak_luminance_set_from_import(self, client, session_id):
        resp = _upload(client, session_id, FULL_CSV)
        peak = resp.json()["session"]["peak_luminance"]
        assert peak == 0.0

    def test_zro_imports_visible_in_session_view(self, client, session_id):
        _upload(client, session_id, FULL_CSV)
        session = client.get(f"/api/session/{session_id}").json()
        assert "zro_imports" in session
        assert len(session["zro_imports"]) == 1

    def test_second_import_appends_to_zro_imports(self, client, session_id):
        _upload(client, session_id, FULL_CSV)
        _upload(client, session_id, CLEAN_GRAYSCALE_CSV)
        session = client.get(f"/api/session/{session_id}").json()
        assert len(session["zro_imports"]) == 2

    def test_import_without_mode_returns_400(self, client):
        # Session with no mode selected yet
        resp = client.post("/api/session", json={"tv_key": "u8g", "simulate": True})
        sid = resp.json()["id"]
        resp = _upload(client, sid, FULL_CSV)
        assert resp.status_code == 400
        assert "mode" in resp.json()["detail"].lower()

    def test_empty_file_returns_400(self, client, session_id):
        resp = _upload(client, session_id, b"")
        assert resp.status_code == 400

    def test_header_only_returns_400(self, client, session_id):
        header_only = b"Date and time\t R\t G\t B\t Y\t x\t y\t msec\n"
        resp = _upload(client, session_id, header_only)
        assert resp.status_code == 400

    def test_garbage_csv_returns_400(self, client, session_id):
        resp = _upload(client, session_id, b"this,is,not,a,zro,file\n1,2,3\n")
        assert resp.status_code == 400

    def test_session_not_found_returns_404(self, client):
        resp = _upload(client, "notreal", FULL_CSV)
        assert resp.status_code == 404

    def test_measurements_deserializable_as_measurement_objects(self, client, session_id):
        _upload(client, session_id, FULL_CSV)
        s = _sessions[session_id]
        # All buckets should contain proper Measurement objects (not raw dicts)
        for bucket in ("pre_measurements", "cms_measurements",
                       "wb_measurements", "gamma_measurements"):
            for m in s[bucket]:
                assert isinstance(m, Measurement), (
                    f"Expected Measurement in {bucket}, got {type(m)}: {m}"
                )
