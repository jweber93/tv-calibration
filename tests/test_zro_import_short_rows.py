"""
Tests for issue #149: malformed ZRO rows (short, None-valued, extra columns)
should not abort the entire import — they are skipped with diagnostics.
"""
import io
import shutil
import tempfile
from pathlib import Path

import pytest

from calibrator.zro_import import (
    ZROImportResult,
    parse_zro_csv,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

HEADER = "Date and time\t R\t G\t B\t Y\t x\t y\t msec\n"

CLEAN_ROWS = (
    "15/03/2026 11:00:00\t16\t16\t16\t0.01\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:03\t26\t26\t26\t0.5\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:06\t51\t51\t51\t2.0\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:09\t77\t77\t77\t5.5\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:12\t102\t102\t102\t10.0\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:15\t128\t128\t128\t18.0\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:18\t153\t153\t153\t27.0\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:21\t179\t179\t179\t38.0\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:24\t204\t204\t204\t48.0\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:27\t230\t230\t230\t56.0\t0.313\t0.329\t 360ms\n"
    "15/03/2026 11:00:30\t235\t235\t235\t60.0\t0.313\t0.329\t 360ms\n"
)


# ── Test: short row does not abort import ────────────────────────────────────

class TestShortRowDoesNotAbort:
    """A single short row (fewer than 7 data columns) must not crash the importer."""

    def test_short_row_in_middle_of_clean_csv(self):
        """Row with only 4 columns (missing Y, x, y, msec) in the middle."""
        csv_bytes = (
            HEADER.encode()
            + b"15/03/2026 11:00:00\t16\t16\t16\n"  # short row
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 1
        assert len(result.row_errors) == 1
        assert "insufficient or empty fields" in result.row_errors[0]
        # The 11 clean rows should still be imported
        assert result.total_rows == 11
        assert len(result.pre_measurements) == 11

    def test_short_row_at_start(self):
        """Short row at the very beginning."""
        csv_bytes = (
            HEADER.encode()
            + b"15/03/2026 11:00:00\t16\t16\n"  # only 3 fields
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 1
        assert result.total_rows == 11

    def test_short_row_at_end(self):
        """Short row appended after all clean rows."""
        csv_bytes = (
            HEADER.encode()
            + CLEAN_ROWS.encode()
            + b"15/03/2026 11:01:00\t16\n"  # only 2 fields
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 1
        assert result.total_rows == 11


# ── Test: None-valued columns (trailing field missing) ───────────────────────

class TestNoneValuedColumns:
    """csv.DictReader fills missing trailing fields with None; these must not crash."""

    def test_missing_trailing_field_yields_none(self):
        """A row with fewer values than header columns gets None for the extras."""
        # csv.DictReader will produce: {'Date and time': '...', ' R': '16', ' G': '16',
        # ' B': '16', ' Y': None, ' x': None, ' y': None, ' msec': None}
        csv_bytes = (
            HEADER.encode()
            + b"15/03/2026 11:00:00\t16\t16\t16\n"  # missing Y, x, y, msec
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 1
        assert result.total_rows == 11

    def test_all_trailing_fields_none(self):
        """Row with only timestamp and one RGB value — rest are all None."""
        csv_bytes = (
            HEADER.encode()
            + b"15/03/2026 11:00:00\t16\n"
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 1
        assert result.total_rows == 11


# ── Test: extra columns (more than header) ───────────────────────────────────

class TestExtraColumns:
    """Rows with more columns than the header should be tolerated."""

    def test_extra_trailing_columns(self):
        """Extra columns are stored under raw_row[None] as a list; should not crash."""
        csv_bytes = (
            HEADER.encode()
            + b"15/03/2026 11:00:00\t16\t16\t16\t0.01\t0.313\t0.329\t 360ms\textra1\textra2\n"
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        # Extra columns become None values in raw_row[None]; they don't affect
        # the first 7 values, so this row should parse fine.
        assert result.unknown_rows == 0
        assert result.total_rows == 12


# ── Test: empty or whitespace-only fields ────────────────────────────────────

class TestEmptyFields:
    """Rows with empty fields in critical positions should be skipped."""

    def test_empty_y_field(self):
        csv_bytes = (
            HEADER.encode()
            + b"15/03/2026 11:00:00\t16\t16\t16\t\t0.313\t0.329\t 360ms\n"
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 1
        assert result.total_rows == 11

    def test_empty_timestamp_field(self):
        csv_bytes = (
            HEADER.encode()
            + b"\t16\t16\t16\t0.01\t0.313\t0.329\t 360ms\n"
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 1
        assert result.total_rows == 11


# ── Test: row_errors cap ─────────────────────────────────────────────────────

class TestRowErrorsCap:
    """Per-row diagnostics must be capped at 20 entries."""

    def test_row_errors_capped_at_20(self):
        """More than 20 bad rows should not overflow row_errors."""
        bad_rows = "\n".join(
            f"15/03/2026 11:00:{i:02d}\t16\t16" for i in range(30)
        )
        csv_bytes = (
            HEADER.encode()
            + bad_rows.encode()
            + b"\n"
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 30
        assert len(result.row_errors) == 20

    def test_row_errors_include_reason(self):
        """Each entry in row_errors should describe why the row was skipped."""
        csv_bytes = (
            HEADER.encode()
            + b"15/03/2026 11:00:00\t16\t16\n"  # short row
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert len(result.row_errors) >= 1
        assert "Row 1" in result.row_errors[0]
        assert "insufficient or empty fields" in result.row_errors[0]


# ── Test: import still works after bad rows ──────────────────────────────────

class TestImportContinuesAfterBadRows:
    """Valid rows after bad rows must still be classified correctly."""

    def test_valid_rows_after_many_bad_rows(self):
        """20 bad rows followed by valid data — all valid rows imported."""
        bad_rows = "\n".join(
            f"15/03/2026 11:00:{i:02d}\t16\t16" for i in range(20)
        )
        csv_bytes = (
            HEADER.encode()
            + bad_rows.encode()
            + b"\n"
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None
        assert result.unknown_rows == 20
        assert result.total_rows == 11
        assert len(result.pre_measurements) == 11
        # Bad rows (11:00:00–11:00:19) and clean rows (11:00:00–11:00:30) overlap
        # in time, so they end up in a single session group.
        assert result.sessions_detected == 1

    def test_merge_after_bad_rows(self):
        """merge_into_session should work normally after bad rows are skipped."""
        csv_bytes = (
            HEADER.encode()
            + b"15/03/2026 11:00:00\t16\t16\n"  # short
            + CLEAN_ROWS.encode()
        )
        result = parse_zro_csv(csv_bytes)
        assert result.fatal_error is None

        session = {}
        from calibrator.zro_import import merge_into_session
        session = merge_into_session(session, result)
        assert "zro_imports" in session
        assert len(session["pre_measurements"]) == 11
