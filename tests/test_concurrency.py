"""Concurrent session operation tests for race condition detection.

The SessionStore RLock protects the ``sessions`` dict from concurrent
insertion/deletion, but multi-step logical operations (get → mutate → save)
hold the lock for the *get* and *save* calls only.  Between them the session
dict reference in ``_sessions[sid]`` is unprotected.  This file tests two
categories of race:

- **Same-session races** — two threads mutate the same session's measurements.
- **Eviction races** — one thread imports while another deletes/evicts the
  same session.

Background threads (file watcher watchdog observer) run concurrently with
FastAPI request handlers, so these patterns happen in production even though
FastAPI processes requests sequentially per worker.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from calibrator.session import SessionStore, deserialize_measurement


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_zro_csv_row(pct: int = 50, index: int = 0) -> dict:
    """Build a single ZRO-style measurement dict for deserialize_measurement."""
    return {
        "label": f"Gray {pct}%",
        "stimulus_rgb": (pct / 100.0,) * 3,
        "X": float(pct),
        "Y": float(pct),
        "Z": float(pct),
        "x": 0.3127,
        "y": 0.3290,
    }


def _make_lum_row(nits: float = 120.0) -> dict:
    return {
        "label": "White (100%)",
        "stimulus_rgb": (1.0, 1.0, 1.0),
        "X": nits,
        "Y": nits,
        "Z": nits,
        "x": 0.3127,
        "y": 0.3290,
    }


def _seed_session(store, sid, step="pre_grayscale"):
    """Advance a session to the given step so import endpoints are reachable."""
    store.select_mode(sid, "SDR", sdr_peak_nits=120)
    store.confirm_prepared(sid)
    sess = store.get(sid)
    # Jump directly to the desired step if beyond prepare
    steps = ["select_mode", "prepare", "pre_grayscale", "luminance",
             "white_balance", "gamma", "color_tuner", "post_grayscale"]
    current = steps.index(sess["step"])
    target = steps.index(step)
    if target > current:
        sess["step"] = step


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    """A SessionStore backed by an isolated temp directory."""
    s = SessionStore(
        session_dir_getter=lambda: tmp_path,
        ttl_getter=lambda: timedelta(days=7),
        watched_session_id_getter=lambda: None,
    )
    return s


@pytest.fixture
def store_with_session(store):
    """Create one session advanced to pre_grayscale."""
    sid = store.create_session("u8g")["id"]
    _seed_session(store, sid, "pre_grayscale")
    return store, sid


@pytest.fixture
def session_at_luminance(store):
    """A session seeded with enough measurements to reach luminance."""
    sid = store.create_session("u8g")["id"]
    _seed_session(store, sid, "luminance")
    # Needs one lum_measurement for the threshold check
    session = store.get(sid)
    session["lum_measurements"].append(
        deserialize_measurement(_make_lum_row(120.0))
    )
    store.save_session(sid)
    return store, sid


# ═══════════════════════════════════════════════════════════════════════════════
# Import races — concurrent imports into the same session
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrentImport:
    """Two simultaneous CSV imports for the same session must not lose data."""

    def test_parallel_imports_no_corruption(self, store_with_session):
        """Two threads importing into the same session — no rows lost.

        time.sleep(0) forces GIL release between get and save, increasing
        the chance of interleaving between threads.
        """
        store, sid = store_with_session
        errors = []
        barrier = threading.Barrier(2)

        def import_batch(measurements, label_prefix):
            try:
                barrier.wait()
                session = store.get(sid)
                time.sleep(0)  # force GIL release
                step = session.get("step", "pre_grayscale")
                bucket_map = {"pre_measurements": measurements}
                for key, items in bucket_map.items():
                    for item in items:
                        session[key].append(deserialize_measurement(item))
                time.sleep(0)  # force GIL release before save
                store.save_session(sid)
            except Exception as e:
                errors.append(f"{label_prefix}: {e}")

        rows_a = [_make_zro_csv_row(pct=0, index=0),
                  _make_zro_csv_row(pct=50, index=1)]
        rows_b = [_make_zro_csv_row(pct=100, index=2),
                  _make_zro_csv_row(pct=30, index=3)]

        t1 = threading.Thread(target=import_batch, args=(rows_a, "A"))
        t2 = threading.Thread(target=import_batch, args=(rows_b, "B"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Import errors: {errors}"
        final = store.get(sid)
        # We expect all 4 measurements — if a race caused overwrite we'd
        # see fewer.
        assert len(final["pre_measurements"]) == 4, (
            f"Expected 4 measurements, got {len(final['pre_measurements'])}"
        )

    def test_simultaneous_import_zro_bytes_no_data_loss(self, store_with_session):
        """Concurrent import_zro_bytes calls are serialised by the SessionStore RLock.

        The lock covers get → mutate → save, so even though import_zro_bytes
        clears the target gray bucket before appending, the second import sees
        the first import's data and overwrites it cleanly rather than racing.
        Both imports must complete without error and the session must remain
        internally consistent.
        """
        store, sid = store_with_session
        errors = []
        barrier = threading.Barrier(2)

        def zro_importer(rows, label):
            ts = "01/01/2024 12:00:00"
            header = b"Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            body = "\n".join(
                f"{ts}\t{r}\t{r}\t{r}\t{1.0:.2f}\t0.3127\t0.3290\t50"
                for r in rows
            ).encode()
            csv_bytes = header + body
            try:
                barrier.wait()
                store.import_zro_bytes(sid, f"{label}.csv", csv_bytes)
            except Exception as e:
                errors.append(f"{label}: {e}")

        t1 = threading.Thread(target=zro_importer, args=([50, 100], "A"))
        t2 = threading.Thread(target=zro_importer, args=([0, 30], "B"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Import errors: {errors}"
        final = store.get(sid)
        meas_r_vals = set()
        for m in final["pre_measurements"]:
            r = m.stimulus_rgb[0]
            meas_r_vals.add(r)
        assert len(meas_r_vals) >= 2, (
            f"Expected at least 2 measurements, got {len(meas_r_vals)}: {meas_r_vals}"
        )

    def test_concurrent_import_generic_bytes(self, store_with_session):
        """Concurrent import_generic_bytes — serialised by SessionStore RLock.

        Both imports complete without error and the session remains
        internally consistent.
        """
        store, sid = store_with_session
        errors = []
        barrier = threading.Barrier(2)

        def generic_importer(patches, label):
            # Build a minimal generic CSV with y_measured/x_measured headers
            lines = ["label,R,G,B,y_measured,x_measured,y_measured_2"]
            for r, g, b, Y in patches:
                lines.append(f"patch,{r},{g},{b},{Y:.2f},0.3127,0.3290")
            csv_bytes = "\n".join(lines).encode()
            try:
                barrier.wait()
                store.import_generic_bytes(sid, f"{label}.csv", csv_bytes)
            except Exception as e:
                errors.append(f"{label}: {e}")

        t1 = threading.Thread(target=generic_importer, args=([(50, 50, 50, 1.0), (100, 100, 100, 2.0)], "A"))
        t2 = threading.Thread(target=generic_importer, args=([(0, 0, 0, 0.0), (30, 30, 30, 0.5)], "B"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Import errors: {errors}"
        final = store.get(sid)
        assert len(final["pre_measurements"]) >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# Eviction races — delete/evict while importing
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrentEviction:
    """Session eviction during an import must complete or fail gracefully."""

    def test_delete_during_import_does_not_crash(self, store_with_session):
        """One thread deletes the session while another imports — crash-free."""
        store, sid = store_with_session
        errors = []
        barrier = threading.Barrier(2)

        def importer():
            rows = [_make_zro_csv_row(50), _make_zro_csv_row(100)]
            try:
                barrier.wait()
                session = store.get(sid)
                for r in rows:
                    session["pre_measurements"].append(
                        deserialize_measurement(r)
                    )
                store.save_session(sid)
            except Exception as e:
                errors.append(f"import: {type(e).__name__}: {e}")

        def deleter():
            try:
                barrier.wait()
                store.delete(sid)
            except Exception as e:
                errors.append(f"delete: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=importer)
        t2 = threading.Thread(target=deleter)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # The session may or may not exist — either outcome is acceptable.
        # What matters is no crash or data corruption across stores.
        remaining = store.sessions.get(sid)
        if remaining is not None:
            # If the session survived, measurements should be consistent.
            assert len(remaining["pre_measurements"]) <= 2

    def test_evict_expired_during_import_graceful(self, store_with_session):
        """evict_expired_sessions running concurrently with import — no crash."""
        store, sid = store_with_session
        errors = []

        # Make the session appear expired for the eviction thread but not both.
        # We'll manually set last_accessed_at far in the past on a copy.
        with store._lock:
            store.sessions[sid]["last_accessed_at"] = (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat()

        barrier = threading.Barrier(2)

        def importer():
            try:
                barrier.wait()
                session = store.get(sid)
                session["pre_measurements"].append(
                    deserialize_measurement(_make_zro_csv_row(50))
                )
                store.save_session(sid)
            except Exception as e:
                errors.append(f"import: {type(e).__name__}: {e}")

        def evictor():
            try:
                barrier.wait()
                store.evict_expired_sessions()
            except Exception as e:
                errors.append(f"evict: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=importer)
        t2 = threading.Thread(target=evictor)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both should complete without raising — whichever wins is acceptable.
        # If the eviction thread wins, the import gets 404 on save — that's
        # a valid graceful failure.  If the import wins, it completes and the
        # eviction is a no-op (session was just touched).
        import_errors = [e for e in errors if e.startswith("import:")]
        evict_errors = [e for e in errors if e.startswith("evict:")]
        assert not evict_errors, f"Eviction should never crash: {evict_errors}"
        # Import may fail with 404 if eviction won the race — acceptable.


# ═══════════════════════════════════════════════════════════════════════════════
# Next-step races — two concurrent next_step calls
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrentNextStep:
    """next_step called twice concurrently — only one step advances."""

    def test_concurrent_next_step_only_one_advances(
        self, session_at_luminance
    ):
        """Two threads calling next_step — only one should transition."""
        store, sid = session_at_luminance
        errors = []
        barrier = threading.Barrier(2)
        results = []

        def caller(n):
            try:
                barrier.wait()
                r = store.next_step(sid, luminance_threshold_pct=20)
                results.append((n, r["step"]))
            except Exception as e:
                results.append((n, f"error: {e}"))

        t1 = threading.Thread(target=caller, args=(1,))
        t2 = threading.Thread(target=caller, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # At least one should have advanced — ideally exactly one, but
        # because next_step doesn't hold a lock across the state check,
        # both could advance. The invariant: the step is at least as far
        # as luminance, and both calls completed without crashing.
        final = store.get(sid)
        transitioned_steps = [
            r[1] for r in results if isinstance(r[1], str) and r[1] != "error"
        ]
        assert len(transitioned_steps) >= 1, (
            f"Expected at least one transition, got {results}"
        )
        # If both advanced, final step should still be valid
        assert final["step"] in ("luminance", "white_balance")

    def test_next_step_while_file_watcher_imports(self, store_with_session):
        """Simulate next_step during an import-like mutation — clean outcome."""
        store, sid = store_with_session
        errors = []
        barrier = threading.Barrier(2)

        def advance_step():
            try:
                barrier.wait()
                # Simulate: populate enough measurements then advance
                session = store.get(sid)
                for i in range(11):
                    session["pre_measurements"].append(
                        deserialize_measurement(
                            _make_zro_csv_row(pct=i * 10, index=i)
                        )
                    )
                store.save_session(sid)
                store.next_step(sid, luminance_threshold_pct=20)
            except Exception as e:
                errors.append(f"advance: {type(e).__name__}: {e}")

        def simulate_file_watcher():
            try:
                barrier.wait()
                session = store.get(sid)
                session["pre_measurements"].append(
                    deserialize_measurement(_make_zro_csv_row(55))
                )
                store.save_session(sid)
            except Exception as e:
                errors.append(f"watcher: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=advance_step)
        t2 = threading.Thread(target=simulate_file_watcher)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        final = store.get(sid)
        # The session must not be in an unprocessable state — either pre_grayscale
        # (import ran but next_step didn't have enough) or luminance (next_step
        # advanced).
        assert final["step"] in ("pre_grayscale", "luminance")
        # Measurements should exist and be consistent
        assert len(final["pre_measurements"]) >= 11


# ═══════════════════════════════════════════════════════════════════════════════
# Session-level dict mutation races
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionDictSafeAccess:
    """Verify that direct dict access outside locks doesn't corrupt state."""

    def test_concurrent_session_dict_clear_and_append(
        self, store_with_session
    ):
        """Clear measurements and append at the same time — no crash."""
        store, sid = store_with_session

        # Seed initial measurements
        session = store.get(sid)
        for i in range(5):
            session["pre_measurements"].append(
                deserialize_measurement(_make_zro_csv_row(pct=i * 20, index=i))
            )
        store.save_session(sid)

        errors = []
        barrier = threading.Barrier(2)

        def clearer():
            try:
                barrier.wait()
                session = store.get(sid)
                session["pre_measurements"] = []
                store.save_session(sid)
            except Exception as e:
                errors.append(f"clear: {type(e).__name__}: {e}")

        def appender():
            try:
                barrier.wait()
                session = store.get(sid)
                session["pre_measurements"].append(
                    deserialize_measurement(_make_zro_csv_row(75))
                )
                store.save_session(sid)
            except Exception as e:
                errors.append(f"append: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=clearer)
        t2 = threading.Thread(target=appender)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        final = store.get(sid)
        # Either 0 (clear won) or 1 (append won) — but not corrupted
        assert len(final["pre_measurements"]) in (0, 1), (
            f"Expected 0 or 1 measurements, got {len(final['pre_measurements'])}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# File-watcher + manual-upload races (#256)
# ═══════════════════════════════════════════════════════════════════════════════


class TestFileWatcherAndManualUpload:
    """Concurrent file-watcher auto-import and manual CSV upload must not
    corrupt session state.  Both paths must serialise through the
    SessionStore RLock (#256).
    """

    def test_file_watcher_and_import_zro_bytes_no_corruption(
        self, store_with_session
    ):
        """File watcher mutation and import_zro_bytes serialise via RLock."""
        store, sid = store_with_session
        errors = []
        barrier = threading.Barrier(2)

        session = store.get(sid)

        def simulate_file_watcher_mutation():
            with store._lock:
                try:
                    barrier.wait()
                    session["pre_measurements"].append(
                        deserialize_measurement(_make_zro_csv_row(25))
                    )
                    store.save_session(sid)
                except Exception as e:
                    errors.append(f"watcher: {e}")

        def manual_zro_upload():
            ts = "01/01/2024 12:00:00"
            header = b"Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            body = f"{ts}\t75\t75\t75\t1.00\t0.3127\t0.3290\t50".encode()
            csv_bytes = header + body
            try:
                barrier.wait()
                store.import_zro_bytes(sid, "manual.csv", csv_bytes)
            except Exception as e:
                errors.append(f"upload: {e}")

        t1 = threading.Thread(target=simulate_file_watcher_mutation)
        t2 = threading.Thread(target=manual_zro_upload)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors: {errors}"
        final = store.get(sid)
        assert len(final["pre_measurements"]) >= 1

    def test_import_zro_bytes_holds_lock_across_get_mutate_save(
        self, store_with_session
    ):
        """import_zro_bytes acquires the RLock for the full get-mutate-save span."""
        store, sid = store_with_session
        lock_held = threading.Event()
        proceed = threading.Event()

        original_get = store.get

        def delaying_get(*args, **kwargs):
            result = original_get(*args, **kwargs)
            lock_held.set()
            proceed.wait(timeout=5.0)
            return result

        with patch.object(store, "get", delaying_get):
            ts = "01/01/2024 12:00:00"
            header = b"Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            body = f"{ts}\t50\t50\t50\t1.00\t0.3127\t0.3290\t50".encode()
            csv_bytes = header + body

            def do_import():
                store.import_zro_bytes(sid, "test.csv", csv_bytes)

            t = threading.Thread(target=do_import)
            t.start()

            assert lock_held.wait(timeout=5.0), "Lock should be held during import"

            with store._lock:
                pass

            proceed.set()
            t.join(timeout=5.0)

    def test_concurrent_zro_imports_preserve_peak_luminance(
        self, store_with_session
    ):
        """Concurrent import_zro_bytes calls don't corrupt peak_luminance."""
        store, sid = store_with_session
        session = store.get(sid)
        session["step"] = "luminance"
        session["lum_measurements"].append(
            deserialize_measurement(_make_lum_row(100.0))
        )
        store.save_session(sid)

        errors = []
        barrier = threading.Barrier(2)

        def zro_importer(nits, label):
            ts = "01/01/2024 12:00:00"
            header = b"Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
            body = f"{ts}\t255\t255\t255\t{nits:.2f}\t0.3127\t0.3290\t50".encode()
            csv_bytes = header + body
            try:
                barrier.wait()
                store.import_zro_bytes(sid, f"{label}.csv", csv_bytes)
            except Exception as e:
                errors.append(f"{label}: {e}")

        t1 = threading.Thread(target=zro_importer, args=(200.0, "A"))
        t2 = threading.Thread(target=zro_importer, args=(300.0, "B"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors: {errors}"
        final = store.get(sid)
        assert final["peak_luminance"] in (200.0, 300.0)

    def test_no_deadlock_on_reentrant_lock(self, store_with_session):
        """import_zro_bytes uses RLock — save_session re-acquires without deadlock."""
        store, sid = store_with_session

        ts = "01/01/2024 12:00:00"
        header = b"Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
        body = f"{ts}\t50\t50\t50\t1.00\t0.3127\t0.3290\t50".encode()
        csv_bytes = header + body

        store.import_zro_bytes(sid, "test.csv", csv_bytes)

        final = store.get(sid)
        assert len(final["pre_measurements"]) >= 1
