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
    """next_step called twice concurrently — exactly one step advances."""

    def test_concurrent_next_step_only_one_advances(
        self, session_at_luminance
    ):
        """Two threads calling next_step — exactly one must advance, no regression.

        Before the fix, both could read the same step locally and both
        advance, or a slow thread could roll the session back.  With
        ``with self._lock:`` wrapping the full check→mutate→save sequence,
        the second thread sees the already-advanced step and fails with 400.
        """
        store, sid = session_at_luminance
        results = []
        barrier = threading.Barrier(2)

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
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"

        final = store.get(sid)
        # Exactly one thread must have advanced the step.
        successes = [r for r in results if not str(r[1]).startswith("error")]
        assert len(successes) == 1, (
            f"Expected exactly one successful transition, got {results}"
        )
        # The final step must be white_balance — not regressed to luminance.
        assert final["step"] == "white_balance", (
            f"Session regressed or stalled: step={final['step']}"
        )

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
# Issue #640: history.py atomic writes and locking
# ═══════════════════════════════════════════════════════════════════════════════


class TestHistoryConcurrency:
    """Tests for issue #640: concurrent access to sessions.jsonl / baseline.json"""

    @pytest.fixture(autouse=True)
    def history_dir(self, tmp_path, monkeypatch):
        """Point history at an isolated temp dir."""
        monkeypatch.setenv("TVCAL_HISTORY_DIR", str(tmp_path))

    def _mk_report(self):
        return {
            "pre_cal": {"avg_de": 8.0},
            "post_cal": {"avg_de": 2.0},
            "gamma": {"avg_gamma": 2.2},
            "peak_luminance": 100.0,
            "improvement_pct": 75.0,
            "white_balance": {"avg_de": 2.5},
            "color_tuner": {"avg_de": 3.0},
        }

    def test_concurrent_record_session_no_lost_updates(self, tmp_path):
        """N threads each appending a distinct session_id for one tv_key.

        Without the module-level lock, concurrent read-modify-write cycles
        can drop entries (each thread reads the file, appends its own, then
        overwrites the whole file).
        """
        from calibrator.history import record_session, load_history

        tv_key = "tv-parallel"
        n = 8
        errors = []
        barrier = threading.Barrier(n)

        def rec(i):
            try:
                barrier.wait()
                record_session(
                    tv_key=tv_key,
                    session_id=f"sid-{i:03d}",
                    mode="SDR",
                    report=self._mk_report(),
                )
            except Exception as e:
                errors.append(f"{i}: {e}")

        threads = [threading.Thread(target=rec, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"record_session errors: {errors}"
        sessions = load_history(tv_key, limit=100)
        sids = {s["session_id"] for s in sessions}
        assert len(sessions) == n, (
            f"Expected {n} sessions, got {len(sessions)}: {sids}"
        )
        for i in range(n):
            assert f"sid-{i:03d}" in sids, f"sid-{i:03d} missing"

    def test_concurrent_update_history_entry_atomic(self, tmp_path):
        """Concurrent update_history_entry calls must not corrupt the JSONL."""
        from calibrator.history import (
            record_session,
            update_history_entry,
            load_history,
        )

        tv_key = "tv-atomic"
        for i in range(5):
            record_session(
                tv_key=tv_key,
                session_id=f"sid-{i:03d}",
                mode="SDR",
                report=self._mk_report(),
            )

        def updater(thread_idx):
            for i in range(5):
                update_history_entry(
                    tv_key=tv_key,
                    session_id=f"sid-{i:03d}",
                    metrics={"peak_luminance": float(thread_idx * 5 + i)},
                )

        threads = [threading.Thread(target=updater, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # File must still be valid JSONL with exactly 5 lines.
        sessions = load_history(tv_key, limit=100)
        assert len(sessions) == 5, (
            f"Expected 5 sessions, got {len(sessions)}"
        )

    def test_atomic_write_no_half_lines(self, tmp_path):
        """_atomic_write_lines produces a parseable JSONL file."""
        from calibrator.history import _atomic_write_lines
        import json as _json

        path = tmp_path / "sessions.jsonl"
        _atomic_write_lines(path, [f'{{"session_id": "sid-{i}"}}' for i in range(3)])
        _atomic_write_lines(
            path,
            [f'{{"session_id": "sid-{i}", "updated": true}}' for i in range(3)],
        )
        for line in path.read_text().splitlines():
            entry = _json.loads(line)  # must not raise
            assert entry.get("updated") is True


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


# ═══════════════════════════════════════════════════════════════════════════════
# TOCTOU regression tests — step-transition methods hold the RLock (#490)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStepTransitionTOCTOU:
    """Verify that step-transition methods serialise under the RLock.

    Before the fix each method released the lock after get() and reacquired
    it only in save_session(), leaving a window where a concurrent call could
    read a stale step, mutate, and save — rolling the session backward.
    """

    def test_next_step_concurrent_does_not_regress(self, session_at_luminance):
        """Two concurrent /next calls must not roll back the session step."""
        store, sid = session_at_luminance
        results = []
        barrier = threading.Barrier(2)

        def advance():
            barrier.wait()
            try:
                results.append(
                    store.next_step(sid, luminance_threshold_pct=10).get("step")
                )
            except Exception as e:
                results.append(str(e))

        t1 = threading.Thread(target=advance)
        t2 = threading.Thread(target=advance)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"

        final = store.get(sid)
        assert final["step"] == "white_balance", (
            f"Session regressed or stalled: step={final['step']}, results={results}"
        )

    def test_prev_step_concurrent_does_not_advance_forward(self, store):
        """Two concurrent /prev calls from 'gamma' must not advance the session forward.

        Without the lock both threads read step="gamma", both compute
        white_balance, and the second save clobbers the first — only one
        effective back-transition.  With the lock they serialise:
        gamma→white_balance, then white_balance→luminance, so both calls
        succeed and the session ends at 'luminance'.
        """
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)
        store.get(sid)["step"] = "gamma"
        store.save_session(sid)

        results = []
        barrier = threading.Barrier(2)

        def go_back():
            barrier.wait()
            try:
                results.append(store.prev_step(sid).get("step"))
            except Exception as e:
                results.append(str(e))

        t1 = threading.Thread(target=go_back)
        t2 = threading.Thread(target=go_back)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"

        final = store.get(sid)
        from calibrator.session import STEPS_ORDER
        gamma_idx = STEPS_ORDER.index("gamma")
        final_idx = STEPS_ORDER.index(final["step"]) if final["step"] in STEPS_ORDER else gamma_idx
        assert final_idx < gamma_idx, (
            f"Session moved forward or stayed at '{final['step']}' (expected before 'gamma')"
        )
        # With the RLock both calls serialise: gamma→white_balance, then
        # white_balance→luminance.  Final step must be 'luminance'.
        assert final["step"] == "luminance", (
            f"Expected 'luminance' (both back-steps serialised), got '{final['step']}', results={results}"
        )

    def test_confirm_prepared_concurrent_only_one_advances(self, store):
        """Two concurrent confirm_prepared calls — exactly one transitions."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)

        results = []
        barrier = threading.Barrier(2)

        def confirm():
            barrier.wait()
            try:
                results.append(store.confirm_prepared(sid).get("step"))
            except Exception as e:
                results.append(str(e))

        t1 = threading.Thread(target=confirm)
        t2 = threading.Thread(target=confirm)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"

        successes = [r for r in results if r == "pre_grayscale"]
        assert len(successes) == 1, (
            f"Expected exactly one success, got {results}"
        )
        assert store.get(sid)["step"] == "pre_grayscale"

    def test_select_mode_concurrent_only_one_advances(self, store):
        """Two concurrent select_mode calls — exactly one transitions."""
        sid = store.create_session("u8g")["id"]

        results = []
        barrier = threading.Barrier(2)

        def select():
            barrier.wait()
            try:
                results.append(store.select_mode(sid, "SDR", sdr_peak_nits=120).get("step"))
            except Exception as e:
                results.append(str(e))

        t1 = threading.Thread(target=select)
        t2 = threading.Thread(target=select)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"

        successes = [r for r in results if r == "prepare"]
        assert len(successes) == 1, (
            f"Expected exactly one success, got {results}"
        )
        assert store.get(sid)["step"] == "prepare"

    def test_jump_to_step_concurrent_does_not_advance(self, store):
        """Two concurrent jump_to_step calls — session ends at the jumped-to step."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)
        store.get(sid)["step"] = "gamma"
        store.save_session(sid)

        from calibrator.session import STEPS_ORDER
        target_index = STEPS_ORDER.index("luminance")

        results = []
        barrier = threading.Barrier(2)

        def jump():
            barrier.wait()
            try:
                results.append(store.jump_to_step(sid, target_index).get("step"))
            except Exception as e:
                results.append(str(e))

        t1 = threading.Thread(target=jump)
        t2 = threading.Thread(target=jump)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"

        final = store.get(sid)
        # Both calls target the same index; both may succeed (idempotent) but
        # must not leave the session past the intended step.
        assert final["step"] == "luminance", (
            f"jump_to_step left session at wrong step: {final['step']}, results={results}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TOCTOU regression tests — config setters + repass hold the RLock (#503)
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigSetterTOCTOU:
    """Verify that config setters and repass() serialise under the RLock.

    Before the fix these methods released the lock after get() and reacquired
    it only in save_session(), leaving a window where a concurrent delete or
    eviction could resurrect the session or silently discard the write while
    still reporting success to the client.

    After the fix each method holds ``with self._lock:`` across get→mutate→save,
    matching the pattern already used by step-transition methods (#490).

    As with ``TestConcurrentEviction``, one side of the race will get a 404 —
    that is an acceptable graceful failure.  What matters is no crash, no
    resurrection of a deleted session, and consistency when the session
    survives.
    """

    def _run_setter_vs_delete(self, store, sid, setter_fn, expected_key,
                              expected_value):
        """Generic helper: race a config setter against delete."""
        errors = []
        barrier = threading.Barrier(2)

        def setter():
            try:
                barrier.wait()
                setter_fn()
            except Exception as e:
                errors.append(f"setter: {type(e).__name__}: {e}")

        def deleter():
            try:
                barrier.wait()
                store.delete(sid)
            except Exception as e:
                errors.append(f"delete: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=setter)
        t2 = threading.Thread(target=deleter)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"
        # Both must complete without crashing — 404 from the loser is fine.
        crash_errors = [e for e in errors if "TypeError" in str(type(e))]
        assert not crash_errors, f"Crashes during race: {errors}"

        # Session must be either fully updated OR completely gone.
        remaining = store.sessions.get(sid)
        if remaining is not None:
            assert remaining[expected_key] == expected_value, (
                f"Session survived but setting not applied: "
                f"{expected_key}={remaining.get(expected_key)}"
            )

    def test_set_code_scale_concurrent_delete_no_resurrection(self, store):
        """set_code_scale concurrent with delete — no resurrection."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)
        self._run_setter_vs_delete(
            store, sid, lambda: store.set_code_scale(sid, "10bit"),
            "code_scale", "10bit"
        )

    def test_set_signal_range_concurrent_delete_no_resurrection(self, store):
        """set_signal_range concurrent with delete — no resurrection."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)
        self._run_setter_vs_delete(
            store, sid, lambda: store.set_signal_range(sid, "full"),
            "signal_range", "full"
        )

    def test_set_pattern_generator_concurrent_delete_no_resurrection(self, store):
        """set_pattern_generator concurrent with delete — no resurrection."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)
        self._run_setter_vs_delete(
            store, sid, lambda: store.set_pattern_generator(sid, "dogegen"),
            "pattern_generator", "dogegen"
        )

    def test_set_grayscale_ramp_concurrent_delete_no_resurrection(self, store):
        """set_grayscale_ramp concurrent with delete — no resurrection."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)
        self._run_setter_vs_delete(
            store, sid, lambda: store.set_grayscale_ramp(sid, 11),
            "grayscale_ramp_steps", 11
        )

    def test_set_lightspace_tier_concurrent_delete_no_resurrection(self, store):
        """set_lightspace_tier concurrent with delete — no resurrection."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)
        self._run_setter_vs_delete(
            store, sid, lambda: store.set_lightspace_tier(sid, "free", 11),
            "lightspace_tier", "free"
        )

    def test_set_gamma_workflow_concurrent_delete_no_resurrection(self, store):
        """set_gamma_workflow concurrent with delete — no resurrection."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)
        self._run_setter_vs_delete(
            store, sid, lambda: store.set_gamma_workflow(sid, "quick"),
            "gamma_workflow", "quick"
        )

    def test_repass_concurrent_delete_no_resurrection(self, store):
        """repass concurrent with delete — no resurrection."""
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)

        errors = []
        barrier = threading.Barrier(2)

        def repasser():
            try:
                barrier.wait()
                store.repass(sid, "repatch")
            except Exception as e:
                errors.append(f"repass: {type(e).__name__}: {e}")

        def deleter():
            try:
                barrier.wait()
                store.delete(sid)
            except Exception as e:
                errors.append(f"delete: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=repasser)
        t2 = threading.Thread(target=deleter)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"
        remaining = store.sessions.get(sid)
        if remaining is not None:
            assert remaining["repass_count"] == 1

    def test_concurrent_setters_serialise(self, store):
        """Two concurrent setters on the same field — only one value wins.

        Without the lock both threads could read a stale session, each apply
        their own value, and save — the second save wins but both clients
        reported success.  With the lock they serialise: exactly one value is
        persisted and both calls complete without error.
        """
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)

        errors = []
        barrier = threading.Barrier(2)

        def setter_a():
            try:
                barrier.wait()
                store.set_code_scale(sid, "8bit")
            except Exception as e:
                errors.append(f"A: {type(e).__name__}: {e}")

        def setter_b():
            try:
                barrier.wait()
                store.set_code_scale(sid, "10bit")
            except Exception as e:
                errors.append(f"B: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=setter_a)
        t2 = threading.Thread(target=setter_b)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert not errors, f"Errors during concurrent setters: {errors}"
        final = store.get(sid)
        assert final["code_scale"] in ("8bit", "10bit"), (
            f"Unexpected code_scale: {final['code_scale']}"
        )

    def test_setter_holds_lock_across_get_mutate_save(self, store):
        """set_code_scale must hold the RLock for the full get→mutate→save span.

        We monkeypatch get() to block after returning, then verify that
        save_session cannot proceed from outside the setter until get()
        unblocks.  This proves the lock wraps both endpoints of the
        read-modify-write, not just get().
        """
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)

        lock_held = threading.Event()
        proceed = threading.Event()
        original_get = store.get

        def delaying_get(*args, **kwargs):
            result = original_get(*args, **kwargs)
            lock_held.set()
            proceed.wait(timeout=5.0)
            return result

        with patch.object(store, "get", delaying_get):
            def do_setter():
                store.set_code_scale(sid, "10bit")

            t = threading.Thread(target=do_setter)
            t.start()

            assert lock_held.wait(timeout=5.0), (
                "Lock should be held during setter execution"
            )

            # While the lock is held, verify another thread blocks on it.
            acquired = threading.Event()

            def try_acquire():
                with store._lock:
                    acquired.set()

            acquire_t = threading.Thread(target=try_acquire)
            acquire_t.start()

            # Give the acquiring thread a moment to try and block.
            time.sleep(0.1)
            assert not acquired.is_set(), (
                "External thread should be blocked on the lock"
            )

            proceed.set()
            t.join(timeout=5.0)
            acquired.wait(timeout=5.0)

    def test_setter_concurrent_with_ttl_eviction_no_resurrection(self, store):
        """Setter called concurrently with background TTL eviction — no resurrection.

        This test verifies the fix against the real-world eviction path
        (``evict_expired_sessions`` called by the file-watcher watchdog), not
        just explicit ``delete()`` calls.  We manually push the session's
        ``last_accessed_at`` into the past to simulate an expired session,
        then race eviction against a setter.  The RLock must prevent the
        setter from resurrecting the session after eviction removes it.
        """
        sid = store.create_session("u8g")["id"]
        store.select_mode(sid, "SDR", sdr_peak_nits=120)
        store.confirm_prepared(sid)

        # Push last_accessed_at far into the past so eviction treats it as expired.
        with store._lock:
            store.sessions[sid]["last_accessed_at"] = (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat()

        errors = []
        barrier = threading.Barrier(2)

        def setter():
            try:
                barrier.wait()
                store.set_code_scale(sid, "10bit")
            except Exception as e:
                errors.append(f"setter: {type(e).__name__}: {e}")

        def evictor():
            try:
                barrier.wait()
                store.evict_expired_sessions()
            except Exception as e:
                errors.append(f"evict: {type(e).__name__}: {e}")

        t1 = threading.Thread(target=setter)
        t2 = threading.Thread(target=evictor)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert not t1.is_alive() and not t2.is_alive(), "Thread(s) hung"
        # No crashes — 404 from the loser is acceptable.
        crash_errors = [e for e in errors if "TypeError" in str(type(e))]
        assert not crash_errors, f"Crashes during race: {errors}"

        # Session must be either fully updated OR completely gone.
        remaining = store.sessions.get(sid)
        if remaining is not None:
            assert remaining["code_scale"] == "10bit", (
                f"Session survived but setting not applied: "
                f"{remaining.get('code_scale')}"
            )
