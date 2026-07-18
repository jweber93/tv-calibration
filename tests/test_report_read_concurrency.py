"""Read-path thread-safety: report generation racing a concurrent import.

The existing ``test_concurrency.py`` suite covers *write*-vs-*write* races
(import vs import, setter vs delete, next_step vs import).  It does **not**
cover the *read*-vs-*write* case that a QE audit flagged: report/quality
endpoints call ``report_payload(store.get(sid))`` / ``session_view(...)`` and
then iterate the session's measurement buckets **outside** the
``SessionStore`` RLock, while the background file-watcher's auto-import mutates
those same buckets **under** the lock (``_locked_import``).

That read path is currently safe, but only because of two properties that are
easy to break in a refactor:

1.  Every write serialises through ``SessionStore._lock``.
2.  ``_locked_import`` **rebinds** the target grayscale bucket
    (``session[key] = []`` then append to the fresh list) rather than mutating
    the reader's list in place, and the append-mode buckets (wb / gamma / cms /
    lum) only ever grow via ``list.append``.  CPython tolerates ``append``
    during iteration; a ``reader`` holding a reference to the old list gets a
    coherent (if momentarily stale) snapshot and never raises.

If a future change replaced the rebind with an in-place ``list.clear()`` /
``list.pop()`` on a bucket a reader is walking (``latest_grayscale_pass`` walks
it with ``reversed()``), or dropped the RLock serialisation, the report read
would start raising ``IndexError`` / ``RuntimeError`` under load.  These tests
pin the safe behaviour: a report read concurrent with heavy imports must never
raise and must always return a structurally coherent payload.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest

from calibrator.reports import report_payload
from calibrator.session import SessionStore, deserialize_measurement, session_view


# ── Helpers ──────────────────────────────────────────────────────────────────


def _gray_row(pct: int, index: int) -> dict:
    """A grayscale measurement dict for ``deserialize_measurement``."""
    code = round(pct / 100.0 * 255)
    return {
        "label": f"{pct}% Gray",
        "stimulus_rgb": (code, code, code),
        "X": float(pct),
        "Y": max(0.1, float(pct)),
        "Z": float(pct),
        "x": 0.3127,
        "y": 0.3290,
        "timestamp": f"2024-01-01T12:00:{index % 60:02d}",
    }


def _cms_row(index: int) -> dict:
    return {
        "label": "Red",
        "stimulus_rgb": (255, 0, 0),
        "X": 41.0,
        "Y": 21.0,
        "Z": 2.0,
        "x": 0.64,
        "y": 0.33,
        "timestamp": f"2024-01-01T12:00:{index % 60:02d}",
    }


def _zro_csv(rows: list[int]) -> bytes:
    """Build a minimal single-session ZRO grayscale export."""
    ts = "01/01/2024 12:00:00"
    header = b"Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
    body = "\n".join(
        f"{ts}\t{r}\t{r}\t{r}\t{max(0.1, r / 2.55):.2f}\t0.3127\t0.3290\t50"
        for r in rows
    ).encode()
    return header + body


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    return SessionStore(
        session_dir_getter=lambda: tmp_path,
        ttl_getter=lambda: timedelta(days=7),
        watched_session_id_getter=lambda: None,
    )


@pytest.fixture
def report_ready_session(store):
    """A session populated across every bucket report_payload reads.

    Seeded at ``post_grayscale`` so both pre/post ramps and the gamma / cms /
    wb / lum buckets are non-empty — report_payload then exercises all of them.
    """
    sid = store.create_session("u8g")["id"]
    store.select_mode(sid, "SDR", sdr_peak_nits=120)
    store.confirm_prepared(sid)
    session = store.get(sid)
    session["step"] = "post_grayscale"
    for i in range(11):
        session["pre_measurements"].append(deserialize_measurement(_gray_row(i * 10, i)))
        session["post_measurements"].append(deserialize_measurement(_gray_row(i * 10, i)))
        session["gamma_measurements"].append(deserialize_measurement(_gray_row(i * 10, i)))
        session["wb_measurements"].append(deserialize_measurement(_gray_row(80, i)))
        session["lum_measurements"].append(deserialize_measurement(_gray_row(100, i)))
        session["cms_measurements"].append(deserialize_measurement(_cms_row(i)))
    store.save_session(sid)
    return store, sid


def _assert_coherent_report(report: dict) -> None:
    """A report snapshot must be structurally valid regardless of interleaving."""
    for key in ("pre_cal", "post_cal", "white_balance", "color_tuner", "gamma"):
        assert key in report, f"report missing '{key}'"
    for section in ("pre_cal", "post_cal", "white_balance", "color_tuner"):
        avg = report[section]["avg_de"]
        assert avg is None or isinstance(avg, (int, float)), (
            f"{section}.avg_de is neither None nor numeric: {avg!r}"
        )
    imp = report["improvement_pct"]
    assert imp is None or isinstance(imp, (int, float))


# ═══════════════════════════════════════════════════════════════════════════════
# Read (report) vs write (import) races
# ═══════════════════════════════════════════════════════════════════════════════


class TestReportReadDuringImport:
    """Report generation must survive a concurrent import of the same session."""

    def test_report_payload_during_direct_bucket_appends(self, report_ready_session):
        """Reader loops report_payload while a writer appends to shared buckets.

        This is the tightest reproduction of the file-watcher-vs-report race:
        the writer appends *in place* to the exact list objects the reader is
        walking (append-mode buckets: gamma / cms / wb / lum), with GIL
        releases (``time.sleep(0)``) forcing interleaving.  Work is bounded on
        both sides so the read stays O(latest-pass), not O(total-appended).
        The reader must never raise and every snapshot must be coherent.
        """
        store, sid = report_ready_session
        reader_errors: list[str] = []
        stop = threading.Event()
        barrier = threading.Barrier(2)

        def writer():
            barrier.wait()
            i = 100
            while not stop.is_set() and i < 100 + 400:
                session = store.sessions[sid]
                for key in (
                    "gamma_measurements",
                    "cms_measurements",
                    "wb_measurements",
                    "lum_measurements",
                ):
                    session.setdefault(key, []).append(
                        deserialize_measurement(_gray_row(50, i))
                    )
                i += 1
                time.sleep(0)  # force GIL release so the reader interleaves

        def reader():
            barrier.wait()
            try:
                for _ in range(200):
                    report = report_payload(store.get(sid))
                    _assert_coherent_report(report)
                    time.sleep(0)
            except Exception as exc:  # noqa: BLE001 - we want the type + message
                reader_errors.append(f"{type(exc).__name__}: {exc}")

        tw = threading.Thread(target=writer)
        tr = threading.Thread(target=reader)
        tw.start()
        tr.start()
        tr.join(timeout=30.0)
        stop.set()
        tw.join(timeout=5.0)

        assert not tr.is_alive() and not tw.is_alive(), "Thread(s) hung"
        assert not reader_errors, f"report_payload raised under concurrent import: {reader_errors}"

    def test_report_payload_during_real_zro_imports(self, report_ready_session):
        """Reader loops report_payload while a writer drives real import_zro_bytes.

        Uses the production ``import_zro_bytes`` path (which rebinds the target
        gray bucket under the RLock) rather than hand-rolled appends, so the
        test also covers the rebind-vs-read interaction end to end.
        """
        store, sid = report_ready_session
        reader_errors: list[str] = []
        writer_errors: list[str] = []
        stop = threading.Event()
        started = threading.Event()

        def writer():
            started.wait(timeout=5.0)
            csv_bytes = _zro_csv([16, 60, 128, 180, 235])
            for _ in range(60):
                if stop.is_set():
                    return
                try:
                    store.import_zro_bytes(sid, "watch.csv", csv_bytes)
                except Exception as exc:  # noqa: BLE001
                    writer_errors.append(f"{type(exc).__name__}: {exc}")
                    return
                time.sleep(0)

        def reader():
            started.set()
            try:
                for _ in range(120):
                    _assert_coherent_report(report_payload(store.get(sid)))
                    # session_view() reads the same buckets via step_quality().
                    session_view(store.get(sid))
                    time.sleep(0)
            except Exception as exc:  # noqa: BLE001
                reader_errors.append(f"{type(exc).__name__}: {exc}")

        tw = threading.Thread(target=writer)
        tr = threading.Thread(target=reader)
        tw.start()
        tr.start()
        tr.join(timeout=30.0)
        stop.set()
        tw.join(timeout=5.0)

        assert not tr.is_alive() and not tw.is_alive(), "Thread(s) hung"
        assert not writer_errors, f"import_zro_bytes raised: {writer_errors}"
        assert not reader_errors, f"read path raised under concurrent import: {reader_errors}"

        # Session must remain internally consistent after the race.
        final = report_payload(store.get(sid))
        _assert_coherent_report(final)
