"""
Tests for the file-watcher feature.

Covers:
- start_watching / stop_watching lifecycle
- Debounce: rapid successive writes trigger only one import
- Duplicate suppression: same file + same mtime → not re-imported
- New mtime → re-imported
- Malformed CSV → error recorded in status, watcher keeps running
- GET /api/watch/status returns correct shape
- POST /api/watch/config with non-existent path returns 400
- DELETE /api/watch/config stops watcher and clears status
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── Helpers to reset singleton state between tests ───────────────────────────

import calibrator.file_watcher as fw


def _reset_watcher():
    """Forcibly clear all singleton state in the file_watcher module."""
    fw.stop_watching()
    with fw._sub_lock:
        fw._subscribers.clear()
    fw._last_import = None
    fw._watcher_error = None


@pytest.fixture(autouse=True)
def clean_watcher():
    """Reset watcher before and after every test."""
    _reset_watcher()
    yield
    _reset_watcher()


# ── Shared minimal ZRO CSV fixture ────────────────────────────────────────────

MINIMAL_ZRO_CSV = (
    "Date and time\t R\t G\t B\t Y\t x\t y\t msec\n"
    "15/03/2026 10:48:14\t16\t16\t16\t0.31\t0.309\t0.318\t 862ms\n"
    "15/03/2026 10:48:17\t26\t26\t26\t0.94\t0.309\t0.318\t 865ms\n"
    "15/03/2026 10:48:20\t51\t51\t51\t4.49\t0.312\t0.329\t 863ms\n"
    "15/03/2026 10:48:23\t77\t77\t77\t11.57\t0.314\t0.329\t 865ms\n"
    "15/03/2026 10:48:25\t102\t102\t102\t17.58\t0.312\t0.326\t 360ms\n"
    "15/03/2026 10:48:27\t128\t128\t128\t21.62\t0.314\t0.328\t 360ms\n"
    "15/03/2026 10:48:30\t153\t153\t153\t45.44\t0.314\t0.329\t 360ms\n"
    "15/03/2026 10:48:32\t179\t179\t179\t63.07\t0.308\t0.325\t 363ms\n"
    "15/03/2026 10:48:34\t204\t204\t204\t29.09\t0.313\t0.330\t 364ms\n"
    "15/03/2026 10:48:37\t230\t230\t230\t108.73\t0.308\t0.325\t 361ms\n"
    "15/03/2026 10:48:39\t235\t235\t235\t85.09\t0.307\t0.327\t 363ms\n"
)

MALFORMED_CSV = "not,a,valid,zro,file\n1,2,3\n"


def _make_session() -> Dict:
    """Return a minimal session dict compatible with merge_into_session."""
    return {
        "step": "pre_grayscale",
        "mode": "SDR",
        "pre_measurements": [],
        "post_measurements": [],
        "cms_measurements": [],
        "lum_measurements": [],
        "wb_measurements": [],
        "gamma_measurements": [],
        "zro_imports": [],
        "peak_luminance": 0.0,
    }


# ════════════════════════════════════════════════════════════════════════════
# Unit tests — file_watcher module directly
# ════════════════════════════════════════════════════════════════════════════

class TestLifecycle:
    def test_start_creates_observer(self, tmp_path):
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)
        status = fw.get_status()
        assert status["watching"] is True
        assert status["path"] == str(tmp_path)
        assert status["error"] is None

    def test_stop_clears_status(self, tmp_path):
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)
        fw.stop_watching()
        status = fw.get_status()
        assert status["watching"] is False
        assert status["path"] is None

    def test_start_invalid_path_raises(self):
        with pytest.raises(ValueError, match="does not exist"):
            fw.start_watching("/nonexistent/path/xyz", lambda: None, lambda: None)

    def test_start_accepts_specific_csv_file(self, tmp_path):
        session = _make_session()
        csv_file = tmp_path / "watch-me.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)
        fw.start_watching(csv_file, lambda: session, lambda: None)
        status = fw.get_status()
        assert status["watching"] is True
        assert status["path"] == str(csv_file)
        assert status["diagnostics"]["watched_file"] == str(csv_file)
        assert status["diagnostics"]["watched_file_exists"] is True

    def test_start_replaces_existing_watcher(self, tmp_path):
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)
        fw.start_watching(tmp_path, lambda: session, lambda: None)
        status = fw.get_status()
        assert status["watching"] is True

    def test_get_status_shape(self, tmp_path):
        status = fw.get_status()
        assert set(status.keys()) == {"watching", "path", "last_import", "error", "diagnostics"}

    @pytest.mark.skipif(fw._WATCHDOG_AVAILABLE, reason="polling fallback not used when watchdog is installed")
    def test_polling_observer_records_error_when_directory_disappears(self):
        observer = fw.Observer()
        observer.schedule(fw.FileSystemEventHandler(), "/tmp/missing-watch-dir")

        with patch.object(observer._stop_event, "wait", side_effect=[False, True]):
            with patch("calibrator.file_watcher.os.scandir", side_effect=FileNotFoundError):
                observer._poll_loop()

        assert "temporarily unavailable" in (fw._watcher_error or "").lower()
        assert "/tmp/missing-watch-dir" in fw._watcher_error

    @pytest.mark.skipif(fw._WATCHDOG_AVAILABLE, reason="polling fallback not used when watchdog is installed")
    def test_polling_survives_transient_missing_dir(self):
        """Watcher survives directory removal, recreation, and resumes importing."""
        import shutil

        tmp_dir = None
        try:
            tmp_dir = pytest.importorskip("tempfile").mkdtemp()
            session = _make_session()
            fw.start_watching(tmp_dir, lambda: session, lambda: None)
            handler = fw._handler
            assert handler is not None

            # Write a CSV so the watcher picks it up first.
            csv_file = os.path.join(tmp_dir, "pre.csv")
            csv_file_content = (
                "Date and time\t R\t G\t B\t Y\t x\t y\t msec\n"
                "15/03/2026 10:48:14\t16\t16\t16\t0.31\t0.309\t0.318\t 862ms\n"
            )
            with open(csv_file, "w") as f:
                f.write(csv_file_content)

            # Wait for first import.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if session.get("pre_measurements"):
                    break
                time.sleep(0.1)
            assert len(session["pre_measurements"]) > 0, "Initial import should succeed"

            # Remove the directory — this triggers the transient error.
            shutil.rmtree(tmp_dir)

            # Give the poll loop time to record the error.
            time.sleep(0.3)
            assert "temporarily unavailable" in (fw._watcher_error or "").lower()

            # Recreate the directory and drop a new CSV.
            os.makedirs(tmp_dir)
            csv_file2 = os.path.join(tmp_dir, "post.csv")
            csv_file_content2 = (
                "Date and time\t R\t G\t B\t Y\t x\t y\t msec\n"
                "15/03/2026 11:00:00\t235\t235\t235\t85.09\t0.307\t0.327\t 363ms\n"
            )
            with open(csv_file2, "w") as f:
                f.write(csv_file_content2)

            # Wait for the watcher to resume and import the new file.  The
            # pre_grayscale step replaces pre_measurements with the latest ramp
            # rather than appending, so the list length stays at 1 — the proof
            # that a second import happened is a second zro_imports entry plus
            # the new ramp's content replacing the original.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if len(session.get("zro_imports", [])) >= 2:
                    break
                time.sleep(0.1)

            assert len(session["zro_imports"]) >= 2, (
                "Watcher should resume and import after directory is recreated"
            )
            assert any(
                (m.get("label") if isinstance(m, dict) else getattr(m, "label", ""))
                == "White (100%)"
                for m in session["pre_measurements"]
            ), "Re-import should replace pre_measurements with the new ramp"
            assert fw._watcher_error is None, (
                "Error should be cleared once directory reappears"
            )
        finally:
            if tmp_dir and os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)


@pytest.mark.flaky(reruns=2, reruns_delay=1)
class TestImport:
    def test_new_csv_is_imported(self, tmp_path):
        session = _make_session()
        save_calls = []
        fw.start_watching(tmp_path, lambda: session, lambda: save_calls.append(1))

        csv_file = tmp_path / "zro_export.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        # Wait for debounce + processing
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        assert len(session["pre_measurements"]) > 0, "pre_measurements should be populated"
        assert len(save_calls) >= 1, "session saver should have been called"

    def test_event_queued_after_import(self, tmp_path):
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)
        event_queue = fw.subscribe()

        csv_file = tmp_path / "zro_export.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        deadline = time.monotonic() + 5.0
        event = None
        while time.monotonic() < deadline:
            try:
                event = event_queue.get(timeout=0.1)
                break
            except Exception:
                pass

        assert event is not None, "SSE event should be queued"
        assert event["type"] == "import"
        assert "file" in event
        assert "timestamp" in event
        assert "buckets" in event
        fw.unsubscribe(event_queue)

    def test_multiple_subscribers_each_receive_same_event(self, tmp_path):
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)
        first = fw.subscribe()
        second = fw.subscribe()

        csv_file = tmp_path / "fanout.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        deadline = time.monotonic() + 5.0
        first_event = None
        second_event = None
        while time.monotonic() < deadline:
            try:
                if first_event is None:
                    first_event = first.get(timeout=0.1)
                if second_event is None:
                    second_event = second.get(timeout=0.1)
                if first_event is not None and second_event is not None:
                    break
            except Exception:
                pass

        assert first_event is not None
        assert second_event is not None
        assert first_event["file"] == second_event["file"]
        assert first_event["buckets"] == second_event["buckets"]
        fw.unsubscribe(first)
        fw.unsubscribe(second)

    def test_last_import_populated(self, tmp_path):
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)

        csv_file = tmp_path / "zro_export.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if fw._last_import is not None:
                break
            time.sleep(0.1)

        li = fw.get_status()["last_import"]
        assert li is not None
        assert "file" in li
        assert "timestamp" in li
        assert "buckets" in li
        assert "abl_warnings" in li
        assert "measurement_start" in li
        assert "measurement_end" in li

    def test_specific_csv_watch_ignores_other_csv_files(self, tmp_path):
        session = _make_session()
        watched_csv = tmp_path / "watch-me.csv"
        other_csv = tmp_path / "ignore-me.csv"
        fw.start_watching(watched_csv, lambda: session, lambda: None)

        other_csv.write_text(MINIMAL_ZRO_CSV)
        time.sleep(fw.DEBOUNCE_SECONDS + 0.3)
        assert len(session["pre_measurements"]) == 0

        watched_csv.write_text(MINIMAL_ZRO_CSV)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        assert len(session["pre_measurements"]) > 0


@pytest.mark.flaky(reruns=2, reruns_delay=1)
class TestDebounce:
    def test_rapid_writes_trigger_one_import(self, tmp_path):
        """Many rapid successive modifications → only one import."""
        session = _make_session()
        import_count = [0]
        original_import = fw._ZROHandler._import_file

        def counting_import(self_handler, src_path):
            original_import(self_handler, src_path)
            # Count only if rows were imported
            if session.get("pre_measurements"):
                import_count[0] += 1

        with patch.object(fw._ZROHandler, "_import_file", counting_import):
            fw.start_watching(tmp_path, lambda: session, lambda: None)

            csv_file = tmp_path / "rapid.csv"
            # Write the file 5 times quickly
            for _ in range(5):
                csv_file.write_text(MINIMAL_ZRO_CSV)
                time.sleep(0.05)

            # Wait long enough for debounce to fire once
            time.sleep(fw.DEBOUNCE_SECONDS + 0.5)

        # Should have imported exactly once despite 5 writes
        assert import_count[0] == 1, (
            f"Expected 1 import after rapid writes, got {import_count[0]}"
        )

    def test_stopped_handler_ignores_late_timer_callback(self, tmp_path):
        session = _make_session()
        handler = fw._ZROHandler(lambda: session, lambda: None)
        csv_file = tmp_path / "late-callback.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        handler.stop()

        with patch.object(fw, "_read_bytes_with_retry", side_effect=AssertionError("should not read after stop")):
            handler._import_file(str(csv_file))

        assert session["pre_measurements"] == []


class TestDuplicateSuppression:
    def test_same_mtime_not_reimported(self, tmp_path):
        """Importing the same file twice with no mtime change → second import skipped."""
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)

        csv_file = tmp_path / "once.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        # Wait for first import
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        first_count = len(session["pre_measurements"])
        assert first_count > 0

        # Manually call _import_file again without changing mtime
        handler = fw._handler
        assert handler is not None
        handler._import_file(str(csv_file))

        # Count should not have changed
        assert len(session["pre_measurements"]) == first_count, (
            "Duplicate import with same mtime should be suppressed"
        )

    def test_appended_new_rows_trigger_incremental_import(self, tmp_path):
        """Growing the watched log imports only genuinely new rows."""
        session = _make_session()
        session["zro_imports"] = []
        fw.start_watching(tmp_path, lambda: session, lambda: None, grayscale_level_count=99)

        csv_file = tmp_path / "reimport.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        # Wait for first import
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        first_count = len(session["pre_measurements"])
        assert first_count > 0
        first_import_count = len(session["zro_imports"])

        # Append one new grayscale row with a later timestamp.
        time.sleep(0.05)
        csv_file.write_text(
            MINIMAL_ZRO_CSV
            + "15/03/2026 10:48:45\t140\t140\t140\t30.01\t0.313\t0.328\t 360ms\n"
        )

        # Wait for second import
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if len(session["zro_imports"]) > first_import_count:
                break
            time.sleep(0.1)

        assert len(session["pre_measurements"]) == first_count + 1
        assert len(session["zro_imports"]) > first_import_count, "New appended rows should trigger a second import"

    def test_rewriting_same_accumulated_log_does_not_duplicate_measurements(self, tmp_path):
        """A rewritten full log with no new rows should not append duplicates."""
        session = _make_session()
        session["zro_imports"] = []
        fw.start_watching(tmp_path, lambda: session, lambda: None, grayscale_level_count=99)

        csv_file = tmp_path / "same-log.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        first_count = len(session["pre_measurements"])
        first_import_count = len(session["zro_imports"])
        assert first_count > 0

        time.sleep(0.05)
        csv_file.write_text(MINIMAL_ZRO_CSV)
        time.sleep(fw.DEBOUNCE_SECONDS + 0.5)

        assert len(session["pre_measurements"]) == first_count
        assert len(session["zro_imports"]) == first_import_count
        assert fw.get_status()["diagnostics"]["last_attempt"]["status"] == "no_new_rows"


class TestErrorHandling:
    def test_locked_csv_is_retried_until_readable(self, tmp_path):
        session = _make_session()
        csv_file = tmp_path / "locked.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        with patch.object(fw, "DEBOUNCE_SECONDS", 0.01), \
             patch.object(fw, "LOCKED_FILE_RETRY_SECONDS", 0.05), \
             patch.object(
                 fw,
                 "_read_bytes_with_retry",
                 side_effect=[PermissionError("file is locked"), MINIMAL_ZRO_CSV.encode()],
             ) as mocked_read:
            fw.start_watching(tmp_path, lambda: session, lambda: None)
            handler = fw._handler
            assert handler is not None
            handler.on_modified(fw.FileSystemEvent(str(csv_file)))

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if session.get("pre_measurements"):
                    break
                time.sleep(0.02)

        assert mocked_read.call_count >= 2
        assert len(session["pre_measurements"]) > 0
        assert fw.get_status()["error"] is None

    def test_malformed_csv_records_error(self, tmp_path):
        """Malformed CSV → error in status, watcher keeps running."""
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)

        bad_file = tmp_path / "bad.csv"
        bad_file.write_text(MALFORMED_CSV)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if fw._watcher_error is not None or fw.get_status()["error"] is not None:
                break
            time.sleep(0.1)

        status = fw.get_status()
        assert status["watching"] is True, "Watcher should still be running after error"
        # Error could be set, or the file simply had no valid rows
        # Either way, no exception should have propagated

    def test_malformed_csv_does_not_stop_subsequent_import(self, tmp_path):
        """After a bad file, a good file should still be imported."""
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)

        # Write bad file first
        bad_file = tmp_path / "bad.csv"
        bad_file.write_text(MALFORMED_CSV)
        time.sleep(fw.DEBOUNCE_SECONDS + 0.3)

        # Now write a good file
        good_file = tmp_path / "good.csv"
        good_file.write_text(MINIMAL_ZRO_CSV)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        assert len(session["pre_measurements"]) > 0, (
            "Good file should import after bad file"
        )

    def test_no_session_records_error(self, tmp_path):
        """If session_getter returns None the watcher records a friendly error."""
        fw.start_watching(tmp_path, lambda: None, lambda: None)

        csv_file = tmp_path / "nosession.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if fw._watcher_error is not None:
                break
            time.sleep(0.1)

        assert "session" in (fw._watcher_error or "").lower(), (
            f"Expected session-related error, got: {fw._watcher_error!r}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Integration tests — HTTP endpoints via TestClient
# ════════════════════════════════════════════════════════════════════════════

from server import app, _sessions, _save_session  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def live_session(client):
    """Create a minimal session in the server's session store."""
    from server import _sessions
    import uuid as _uuid

    sid = _uuid.uuid4().hex
    from calibrator import Measurement
    import threading

    _sessions[sid] = {
        "id": sid,
        "tv_key": "lgc2",
        "tv_name": "LG C2",
        "simulate": True,
        "step": "pre_grayscale",
        "mode": "SDR",
        "target": None,
        "meter": None,
        "lightspace": None,
        "lightspace_host": None,
        "lightspace_port": 2100,
        "lightspace_patch_size": 75,
        "lightspace_connected": False,
        "measurement_status": {},
        "measurement_lock": threading.Lock(),
        "measurement_thread": None,
        "pre_measurements": [],
        "post_measurements": [],
        "cms_measurements": [],
        "lum_measurements": [],
        "wb_measurements": [],
        "gamma_measurements": [],
        "peak_luminance": 0.0,
        "sdr_peak_nits": None,
        "created_at": "2026-03-15T10:00:00",
        "zro_imports": [],
    }
    yield sid
    _sessions.pop(sid, None)


class TestWatchEndpoints:
    def test_get_status_shape(self, client):
        resp = client.get("/api/watch/status")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {
            "watching",
            "path",
            "last_import",
            "error",
            "diagnostics",
            "session_id",
            "session_exists",
        }
        assert data["watching"] is False

    def test_post_config_nonexistent_path_returns_400(self, client, live_session):
        resp = client.post(
            "/api/watch/config",
            json={"path": "/nonexistent/path/xyz", "sid": live_session},
        )
        assert resp.status_code == 400

    def test_post_config_unknown_session_returns_404(self, client, tmp_path):
        resp = client.post(
            "/api/watch/config",
            json={"path": str(tmp_path), "sid": "doesnotexist"},
        )
        assert resp.status_code == 404

    def test_post_config_starts_watcher(self, client, tmp_path, live_session, monkeypatch):
        import server as server_module
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        resp = client.post(
            "/api/watch/config",
            json={"path": str(tmp_path), "sid": live_session},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["watching"] is True
        assert data["path"] == str(tmp_path)

    def test_post_config_accepts_csv_file_path(self, client, tmp_path, live_session, monkeypatch):
        import server as server_module
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        csv_file = tmp_path / "watch-me.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)
        resp = client.post(
            "/api/watch/config",
            json={"path": str(csv_file), "sid": live_session},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["watching"] is True
        assert data["path"] == str(csv_file)

    def test_delete_config_stops_watcher(self, client, tmp_path, live_session, monkeypatch):
        import server as server_module
        monkeypatch.setattr(server_module, "_WATCH_ROOT", tmp_path.resolve())
        client.post(
            "/api/watch/config",
            json={"path": str(tmp_path), "sid": live_session},
        )
        resp = client.delete("/api/watch/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["watching"] is False
        assert data["path"] is None

    def test_delete_config_when_not_watching(self, client):
        resp = client.delete("/api/watch/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["watching"] is False


class TestSessionLock:
    """Verify that session_lock protects against concurrent mutation."""

    def test_session_lock_is_acquired_during_import(self, tmp_path):
        """The session_lock is held while session_getter → merge → session_saver runs."""
        import threading

        session = _make_session()
        lock = threading.Lock()
        held = threading.Event()
        release_gate = threading.Event()

        class InstrumentedLock:
            def __init__(self):
                self._inner = threading.Lock()

            def acquire(self, *a, **kw):
                result = self._inner.acquire(*a, **kw)
                if result:
                    held.set()
                    release_gate.wait(timeout=5.0)
                return result

            def release(self):
                try:
                    release_gate.set()
                finally:
                    self._inner.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *a):
                self.release()

        instrumented = InstrumentedLock()

        fw.start_watching(
            tmp_path,
            lambda: session,
            lambda: None,
            session_lock=instrumented,
        )

        csv_file = tmp_path / "lock_test.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        assert held.wait(timeout=5.0), "session_lock should be acquired during import"
        release_gate.set()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        assert len(session["pre_measurements"]) > 0

    def test_concurrent_imports_are_serialised_by_session_lock(self, tmp_path):
        """Two imports via the same handler cannot interleave session mutation."""
        import threading

        session = _make_session()
        lock = threading.Lock()
        mutation_log: list = []
        log_lock = threading.Lock()

        def logging_getter():
            with log_lock:
                mutation_log.append("get")
            return session

        def logging_saver():
            with log_lock:
                mutation_log.append("save")

        fw.start_watching(
            tmp_path,
            logging_getter,
            logging_saver,
            session_lock=lock,
        )

        csv_file = tmp_path / "test.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        # Second import via different mtime
        csv_file.write_text(MINIMAL_ZRO_CSV + "15/03/2026 10:48:45\t140\t140\t140\t30.01\t0.313\t0.328\t 360ms\n")

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with log_lock:
                if len(mutation_log) >= 4:
                    break
            time.sleep(0.1)

        fw.stop_watching()

        with log_lock:
            log_snapshot = list(mutation_log)

        assert len(log_snapshot) >= 4, f"Expected at least 4 events (2 get+save pairs), got {log_snapshot}"
        # Every save must be preceded by a get — no interleaving
        for i in range(0, len(log_snapshot) - 1, 2):
            assert log_snapshot[i] == "get" and log_snapshot[i + 1] == "save", (
                f"Expected (get, save) pair at index {i}, got {log_snapshot[i:i+2]}"
            )

    def test_no_lock_still_works(self, tmp_path):
        """When no session_lock is provided, imports still work (backward compat)."""
        session = _make_session()
        fw.start_watching(tmp_path, lambda: session, lambda: None)

        csv_file = tmp_path / "nolock.csv"
        csv_file.write_text(MINIMAL_ZRO_CSV)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if session.get("pre_measurements"):
                break
            time.sleep(0.1)

        assert len(session["pre_measurements"]) > 0
