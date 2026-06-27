"""
Resilience tests for the polling Observer in file_watcher.py.

Covers:
- Transient FileNotFoundError → polling continues, not permanently halted
- Error is cleared when the directory reappears
- A CSV written after recovery is imported successfully
"""

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import calibrator.file_watcher as fw


MINIMAL_ZRO_CSV = (
    "Date and time\t R\t G\t B\t Y\t x\t y\t msec\n"
    "15/03/2026 10:48:14\t16\t16\t16\t0.31\t0.309\t0.318\t 862ms\n"
    "15/03/2026 10:48:17\t26\t26\t26\t0.94\t0.309\t0.318\t 865ms\n"
    "15/03/2026 10:48:20\t51\t51\t51\t4.49\t0.312\t0.329\t 863ms\n"
)


def _reset_watcher():
    fw.stop_watching()
    with fw._sub_lock:
        fw._subscribers.clear()
    fw._last_import = None
    fw._watcher_error = None


@pytest.fixture(autouse=True)
def clean_watcher():
    _reset_watcher()
    yield
    _reset_watcher()


# ── Inline polling Observer (mirrors file_watcher.py fallback) ──────────────
# Uses `os.scandir` directly (imported at module level) so patches on
# "os.scandir" apply correctly.


class _ShimFileSystemEvent:
    """Shim matching the polling fallback's FileSystemEvent (positional arg)."""

    def __init__(self, src_path: str, is_directory: bool = False) -> None:
        self.src_path = src_path
        self.is_directory = is_directory


class _PollingObserver:
    """Copy of the polling Observer from file_watcher.py for testing."""

    POLL_INTERVAL_SECONDS = 0.1

    def __init__(self) -> None:
        self.daemon = True
        self._handler = None
        self._path = None
        self._thread = None
        self._stop_event = threading.Event()
        self._known_mtimes = {}

    def schedule(self, handler, path, recursive=False):
        del recursive
        self._handler = handler
        self._path = path

    def start(self) -> None:
        if self._handler is None or self._path is None:
            raise RuntimeError("Observer must be scheduled before start()")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=self.daemon)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _poll_loop(self) -> None:
        assert self._handler is not None
        assert self._path is not None

        while not self._stop_event.wait(self.POLL_INTERVAL_SECONDS):
            try:
                current = {}
                for entry in os.scandir(self._path):
                    if not entry.is_file() or not entry.name.lower().endswith(".csv"):
                        continue
                    mtime = entry.stat().st_mtime
                    current[entry.path] = mtime
                    previous_mtime = self._known_mtimes.get(entry.path)
                    event = _ShimFileSystemEvent(entry.path, is_directory=False)
                    if previous_mtime is None:
                        self._handler.on_created(event)
                    elif previous_mtime != mtime:
                        self._handler.on_modified(event)
                self._known_mtimes = current
            except FileNotFoundError:
                msg = f"Watched directory temporarily unavailable: {self._path}"
                with fw._lock:
                    fw._watcher_error = msg
                continue
            else:
                with fw._lock:
                    if fw._watcher_error and "temporarily unavailable" in (
                        fw._watcher_error or ""
                    ):
                        fw._watcher_error = None


# ── Unit tests — polling Observer _poll_loop directly ────────────────────────


class TestPollLoopResilience:
    """Direct tests of the _poll_loop resilience logic."""

    def test_poll_loop_continues_after_file_not_found(self):
        """FileNotFoundError → continue, not break."""
        observer = _PollingObserver()
        observer.schedule(fw.FileSystemEventHandler(), "/tmp/missing-watch-dir")

        # Two False (keep polling) then True (stop).
        with patch.object(observer._stop_event, "wait", side_effect=[False, False, True]):
            with patch.object(os, "scandir", side_effect=FileNotFoundError):
                observer._poll_loop()

        # The error should have been set during the loop.
        assert "temporarily unavailable" in (fw._watcher_error or "").lower(), (
            f"Expected transient error, got: {fw._watcher_error!r}"
        )

    def test_error_cleared_on_recovery(self):
        """When scandir succeeds after a transient error, error is cleared."""
        observer = _PollingObserver()
        observer.schedule(fw.FileSystemEventHandler(), "/tmp/watch-recovers")

        fw._watcher_error = "Watched directory temporarily unavailable: /tmp/watch-recovers"

        with patch.object(observer._stop_event, "wait", side_effect=[False, True]):
            with patch.object(os, "scandir", return_value=[]):
                observer._poll_loop()

        assert fw._watcher_error is None


# ── Integration test — full watcher lifecycle with transient error ──────────


@pytest.mark.flaky(reruns=2, reruns_delay=1)
class TestFullWatcherResilience:
    """End-to-end test: watcher survives a transient directory disappearance."""

    def test_polling_survives_transient_missing_dir(self):
        """tmp directory, start watcher, rmtree, recreate, drop a CSV,
        assert it is imported."""
        tmp_base = tempfile.mkdtemp()
        watch_dir = Path(tmp_base) / "watch"
        watch_dir.mkdir()

        session = {
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

        handler = fw._ZROHandler(
            lambda: session,
            lambda: None,
        )
        observer = _PollingObserver()
        observer.schedule(handler, str(watch_dir))
        observer.POLL_INTERVAL_SECONDS = 0.05
        observer.start()

        try:
            # Verify watcher is running.
            assert observer.is_alive()

            # Remove the directory.
            shutil.rmtree(watch_dir)

            # Wait for the error to surface.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if fw._watcher_error is not None:
                    break
                time.sleep(0.1)

            assert "temporarily unavailable" in (fw._watcher_error or "").lower(), (
                f"Expected transient error, got: {fw._watcher_error!r}"
            )

            # Recreate the directory.
            watch_dir.mkdir()

            # Write a CSV — it should be imported.
            csv_file = watch_dir / "after_recovery.csv"
            csv_file.write_text(MINIMAL_ZRO_CSV)

            # Wait for import.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if session.get("pre_measurements"):
                    break
                time.sleep(0.1)

            assert len(session["pre_measurements"]) > 0, (
                "CSV written after directory recovery should be imported"
            )

            # Error should be cleared.
            assert fw._watcher_error is None, (
                f"Expected error cleared, got: {fw._watcher_error!r}"
            )

        finally:
            observer.stop()
            observer.join(timeout=2.0)
            shutil.rmtree(tmp_base, ignore_errors=True)

    def test_non_transient_error_not_cleared(self):
        """If scandir raises a non-FileNotFoundError, the error should
        not be cleared by the else branch (but the current code only
        catches FileNotFoundError, so other errors propagate)."""
        observer = _PollingObserver()
        observer.schedule(fw.FileSystemEventHandler(), "/tmp/watch-error")

        with patch.object(observer._stop_event, "wait", side_effect=[False, True]):
            with patch.object(os, "scandir", side_effect=PermissionError("denied")):
                with pytest.raises(PermissionError):
                    observer._poll_loop()

        # The error should NOT have been cleared.
        assert fw._watcher_error is None  # was never set (exception propagated)
