"""
Deduplication in merge_into_session — regression tests for #145.

Ensures that re-importing the same ZROImportResult into a session does not
duplicate measurements, and that the seeding of ``existing_keys`` from the
session's current buckets works correctly.
"""

import csv
import io
from pathlib import Path

import pytest

from calibrator.zro_import import (
    ZROImportResult,
    parse_zro_csv,
    merge_into_session,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def zro_csv_full() -> str:
    """A minimal ZRO CSV with colour patches + grayscale ramp."""
    return (
        "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
        "01/01/2025 10:00:00\t1023\t0\t0\t180.5\t0.734\t0.265\t100\n"
        "01/01/2025 10:00:02\t0\t1023\t0\t175.2\t0.140\t0.800\t100\n"
        "01/01/2025 10:00:04\t0\t0\t1023\t170.8\t0.120\t0.080\t100\n"
        "01/01/2025 10:00:06\t1023\t1023\t1023\t200.0\t0.312\t0.329\t100\n"
        "01/01/2025 10:00:10\t255\t255\t255\t195.3\t0.314\t0.330\t100\n"
        "01/01/2025 10:00:15\t128\t128\t128\t90.0\t0.310\t0.325\t100\n"
        "01/01/2025 10:00:20\t64\t64\t64\t40.0\t0.308\t0.322\t100\n"
        "01/01/2025 10:00:25\t16\t16\t16\t2.5\t0.305\t0.318\t100\n"
    )


@pytest.fixture()
def zro_import_result(zro_csv_full: str) -> ZROImportResult:
    """Parse the fixture CSV into a ZROImportResult."""
    return parse_zro_csv(zro_csv_full)


# ── tests ─────────────────────────────────────────────────────────────────────


class TestReimportDoesNotDuplicate:
    """Merge the same ZROImportResult twice — second call must add nothing."""

    def test_reimport_does_not_duplicate(self, zro_import_result: ZROImportResult):
        session: dict = {}

        # First import — populates buckets.
        merge_into_session(session, zro_import_result)
        first_cms = len(session.get("cms_measurements", []))
        first_pre = len(session.get("pre_measurements", []))

        # Second import with the *same* result — no duplicates allowed.
        merge_into_session(session, zro_import_result)
        assert len(session["cms_measurements"]) == first_cms
        assert len(session["pre_measurements"]) == first_pre

    def test_reimport_counts_duplicates(self, zro_import_result: ZROImportResult):
        session: dict = {}
        merge_into_session(session, zro_import_result)
        merge_into_session(session, zro_import_result)

        last_meta = session["zro_imports"][-1]
        assert last_meta["duplicates_skipped"] > 0


class TestEmptySessionFirstImport:
    """First import into a fresh session should work normally."""

    def test_all_buckets_populated(self, zro_import_result: ZROImportResult):
        session: dict = {}
        merge_into_session(session, zro_import_result)

        assert len(session["cms_measurements"]) > 0
        assert len(session["pre_measurements"]) > 0
        assert len(session["zro_imports"]) == 1

    def test_import_meta_recorded(self, zro_import_result: ZROImportResult):
        session: dict = {}
        merge_into_session(session, zro_import_result)

        meta = session["zro_imports"][0]
        assert meta["total_rows"] == zro_import_result.total_rows
        # duplicates_skipped may be > 0 on first import because the same
        # measurement can legitimately appear in multiple buckets (e.g. a
        # white patch goes to both cms_measurements and lum_measurements).
        # What matters is that re-imports don't add more duplicates.
        assert meta["buckets_populated"] == {
            "cms_measurements": len(zro_import_result.cms_measurements),
            "pre_measurements": len(zro_import_result.pre_measurements),
            "lum_measurements": len(zro_import_result.lum_measurements),
            "wb_measurements": len(zro_import_result.wb_measurements),
            "gamma_measurements": len(zro_import_result.gamma_measurements),
        }


class TestPartialSession:
    """Re-import when some buckets already have data."""

    def test_new_rows_appended(self, zro_import_result: ZROImportResult):
        session: dict = {}
        session["cms_measurements"] = [
            {"timestamp": "2025-01-01T09:00:00", "r": 0, "g": 0, "b": 0}
        ]
        merge_into_session(session, zro_import_result)

        # Existing row preserved + new rows from import.
        assert len(session["cms_measurements"]) == 1 + len(
            zro_import_result.cms_measurements
        )

    def test_overlapping_rows_deduplicated(self, zro_import_result: ZROImportResult):
        session: dict = {}
        # Pre-seed with one measurement that matches the first CMS row.
        first_row = zro_import_result.cms_measurements[0]
        session["cms_measurements"] = [dict(first_row)]

        merge_into_session(session, zro_import_result)

        # The seeded row is a duplicate, so cms_measurements should equal
        # the imported count (the seeded row is NOT double-counted).
        assert len(session["cms_measurements"]) == len(
            zro_import_result.cms_measurements
        )


class TestFatalError:
    """merge_into_session should raise on fatal_error."""

    def test_fatal_error_raises(self):
        result = ZROImportResult(fatal_error="CSV parse failed")
        with pytest.raises(ValueError, match="CSV parse failed"):
            merge_into_session({}, result)


class TestCrossBucketDedup:
    """Per-bucket dedup allows same measurement to coexist across buckets."""

    def test_same_measurement_in_multiple_buckets(self):
        """The same physical reading should exist in multiple buckets.

        This test was updated to reflect the fix for #262 — per-bucket dedup
        allows a measurement to coexist across buckets (e.g., white patch in
        both cms_measurements and lum_measurements).
        """
        result = ZROImportResult(cms_measurements=[], pre_measurements=[])

        m = {
            "timestamp": "2025-01-01T10:00:00",
            "r": 255,
            "g": 255,
            "b": 255,
        }
        result.cms_measurements.append(m)
        result.pre_measurements.append(m)

        session: dict = {}
        merge_into_session(session, result)

        # With per-bucket dedup, both buckets should have the measurement
        assert len(session["cms_measurements"]) == 1
        assert len(session["pre_measurements"]) == 1

        # No duplicates should be counted since they're in different buckets
        assert session["zro_imports"][-1]["duplicates_skipped"] == 0

    def test_reimport_dedup_within_bucket(self):
        """Re-importing the same measurement should deduplicate within a bucket."""
        result = ZROImportResult(
            cms_measurements=[{
                "timestamp": "2025-01-01T10:00:00",
                "r": 255,
                "g": 255,
                "b": 255,
            }]
        )

        session: dict = {}
        merge_into_session(session, result)
        first_count = len(session["cms_measurements"])

        # Re-import the same result
        merge_into_session(session, result)

        # Should not duplicate within the bucket
        assert len(session["cms_measurements"]) == first_count
        last_meta = session["zro_imports"][-1]
        assert last_meta["duplicates_skipped"] == 1


class TestMeasurementKeyHandlesDataclass:
    """_measurement_key should work with Measurement dataclass instances."""

    def test_key_with_dataclass_measurement(self):
        """_measurement_key must handle Measurement dataclass, not just dicts."""
        from calcore.models import Measurement
        from calibrator.zro_import import _measurement_key

        m = Measurement(
            x=0.3127, y=0.3290, Y=100.0,
            X=95.047, Z=108.883,
            timestamp="2025-01-01T10:00:00",
            label="100% Gray",
            stimulus_rgb=(255, 255, 255),
        )
        key = _measurement_key(m)
        assert key == "2025-01-01T10:00:00:255:255:255"

    def test_key_with_dict_measurement(self):
        """_measurement_key still works with plain dicts."""
        from calibrator.zro_import import _measurement_key

        m = {
            "timestamp": "2025-01-01T10:00:00",
            "stimulus_rgb": (255, 255, 255),
        }
        key = _measurement_key(m)
        assert key == "2025-01-01T10:00:00:255:255:255"

    def test_dedup_works_with_dataclass_measurements(self):
        """Re-importing with Measurement dataclass instances should dedup."""
        from calcore.models import Measurement
        from calibrator.zro_import import _measurement_key

        m1 = Measurement(
            x=0.3127, y=0.3290, Y=100.0,
            X=95.047, Z=108.883,
            timestamp="2025-01-01T10:00:00",
            label="100% Gray",
            stimulus_rgb=(255, 255, 255),
        )
        m1_dict = _measurement_key(m1)

        m2 = Measurement(
            x=0.3127, y=0.3290, Y=100.0,
            X=95.047, Z=108.883,
            timestamp="2025-01-01T10:00:00",
            label="100% Gray",
            stimulus_rgb=(255, 255, 255),
        )
        m2_dict = _measurement_key(m2)

        assert m1_dict == m2_dict


class TestMultiBucketMembership:
    """Same physical reading should coexist in multiple buckets.

    Regression test for #262 — merge_into_session() uses a single dedup set
    shared across all buckets, so any grayscale reading that legitimately
    appears in multiple result buckets is kept only in the first bucket
    processed and dropped from the rest.
    """

    def test_grayscale_row_in_multiple_buckets(self):
        """A 5-row ramp that parses to wb=2, gamma=5 should populate all buckets.

        This is the exact example from issue #262 demonstrating the bug where
        wb_measurements and gamma_measurements end up empty because the same
        physical reading is deduplicated across buckets.
        """
        from calibrator.zro_import import parse_zro_csv, merge_into_session

        csv_content = (
            "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            "01/01/2025 10:00:00\t16\t16\t16\t0.3\t0.31\t0.32\t100\n"
            "01/01/2025 10:00:02\t77\t77\t77\t18.0\t0.312\t0.329\t100\n"
            "01/01/2025 10:00:04\t128\t128\t128\t70.0\t0.311\t0.328\t100\n"
            "01/01/2025 10:00:06\t204\t204\t204\t150.0\t0.313\t0.329\t100\n"
            "01/01/2025 10:00:08\t235\t235\t235\t200.0\t0.312\t0.329\t100\n"
        )

        res = parse_zro_csv(csv_content)
        sess: dict = {}
        merge_into_session(sess, res)

        # Pre-measurements should have all 5 grayscale rows
        assert len(sess["pre_measurements"]) == 5

        # WB measurements should have 2 rows (30% and 80% gray)
        assert len(sess["wb_measurements"]) == 2, (
            f"Expected 2 wb rows (30%+80% gray), got {len(sess['wb_measurements'])}"
        )

        # Gamma measurements should have 5 rows (all snapped 5% steps)
        assert len(sess["gamma_measurements"]) == 5, (
            f"Expected 5 gamma rows (5% steps), got {len(sess['gamma_measurements'])}"
        )

    def test_per_bucket_dedup_still_works(self):
        """Re-importing the same CSV should deduplicate within each bucket.

        Verifies that per-bucket dedup doesn't break re-import idempotency.
        """
        from calibrator.zro_import import parse_zro_csv, merge_into_session

        csv_content = (
            "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            "01/01/2025 10:00:00\t77\t77\t77\t18.0\t0.312\t0.329\t100\n"
            "01/01/2025 10:00:02\t204\t204\t204\t150.0\t0.313\t0.329\t100\n"
        )

        res = parse_zro_csv(csv_content)
        sess: dict = {}

        # First import
        merge_into_session(sess, res)
        first_wb = len(sess["wb_measurements"])
        first_gamma = len(sess["gamma_measurements"])

        # Second import with same result — should deduplicate
        merge_into_session(sess, res)

        assert len(sess["wb_measurements"]) == first_wb
        assert len(sess["gamma_measurements"]) == first_gamma

        # Meta should show exactly 6 duplicates (2 rows × 3 buckets: pre, wb, gamma)
        last_meta = sess["zro_imports"][-1]
        assert last_meta["duplicates_skipped"] == 6
