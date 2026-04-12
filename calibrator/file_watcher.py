"""
Background file watcher for automatic ZRO CSV import.

Watches a configured directory for new or modified ``.csv`` files and
auto-imports them into the active calibration session via the same
``parse_zro_csv`` / ``merge_into_session`` pipeline used by the manual
upload endpoint.

Usage (from server.py)::

    from calibrator.file_watcher import start_watching, stop_watching, get_status

    start_watching(
        path="/Volumes/ZRO-exports",
        session_getter=lambda: _sessions.get(sid),
        session_saver=lambda: _save_session(sid),
        measurement_deserializer=_deserialize_measurement,
        grayscale_level_count=len(GRAYSCALE_LEVELS),
    )
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ModuleNotFoundError:
    _WATCHDOG_AVAILABLE = False

    class FileSystemEvent:
        """Minimal watchdog-compatible event used by the polling fallback."""

        def __init__(self, src_path: str, is_directory: bool = False) -> None:
            self.src_path = src_path
            self.is_directory = is_directory

    class FileSystemEventHandler:
        """Compatibility shim for environments without watchdog installed."""

        def on_created(self, event: FileSystemEvent) -> None:
            return None

        def on_modified(self, event: FileSystemEvent) -> None:
            return None

    class Observer:
        """
        Tiny polling-based replacement for watchdog.Observer.

        This keeps auto-import working in minimal environments where the
        optional watchdog dependency is not installed, including CI.
        """

        POLL_INTERVAL_SECONDS = 0.1

        def __init__(self) -> None:
            self.daemon = True
            self._handler: Optional[FileSystemEventHandler] = None
            self._path: Optional[str] = None
            self._thread: Optional[threading.Thread] = None
            self._stop_event = threading.Event()
            self._known_mtimes: Dict[str, float] = {}

        def schedule(
            self, handler: FileSystemEventHandler, path: str, recursive: bool = False
        ) -> None:
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

        def join(self, timeout: Optional[float] = None) -> None:
            if self._thread is not None:
                self._thread.join(timeout=timeout)

        def is_alive(self) -> bool:
            return self._thread is not None and self._thread.is_alive()

        def _poll_loop(self) -> None:
            assert self._handler is not None
            assert self._path is not None

            while not self._stop_event.wait(self.POLL_INTERVAL_SECONDS):
                try:
                    current: Dict[str, float] = {}
                    for entry in os.scandir(self._path):
                        if not entry.is_file() or not entry.name.lower().endswith(
                            ".csv"
                        ):
                            continue
                        mtime = entry.stat().st_mtime
                        current[entry.path] = mtime
                        previous_mtime = self._known_mtimes.get(entry.path)
                        event = FileSystemEvent(entry.path, is_directory=False)
                        if previous_mtime is None:
                            self._handler.on_created(event)
                        elif previous_mtime != mtime:
                            self._handler.on_modified(event)
                    self._known_mtimes = current
                except FileNotFoundError:
                    msg = f"Watched directory no longer exists: {self._path}"
                    logger.error(msg)
                    with _lock:
                        global _watcher_error
                        _watcher_error = msg
                    break


from .zro_import import ZROImportResult, parse_zro_csv


def _read_bytes_with_retry(path: str) -> bytes:
    """
    Read file bytes, retrying on PermissionError.

    ZRO on Windows often holds an exclusive lock on the CSV while flushing.
    The debounce reduces the window, but a short retry loop handles the
    remaining cases without failing the whole import.
    """
    last_exc: Exception = OSError("no attempts made")
    for attempt in range(FILE_READ_RETRIES):
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except PermissionError as exc:
            last_exc = exc
            if attempt < FILE_READ_RETRIES - 1:
                time.sleep(FILE_READ_RETRY_DELAY)
    raise last_exc


def _bucket_map_for_session_step(
    result: ZROImportResult, session_step: Optional[str]
) -> Dict[str, List[dict]]:
    """
    Map parsed import buckets into session buckets for the current workflow step.

    ZRO exports a standalone grayscale ramp as the "first" grayscale session in
    that file, so parse_zro_csv classifies it into pre_measurements by default.
    When the active session is already in post_grayscale we need to treat that
    incoming ramp as post-cal data instead.
    """
    bucket_map: Dict[str, List[dict]] = {
        "cms_measurements": [],
        "pre_measurements": [],
        "post_measurements": [],
        "lum_measurements": [],
        "wb_measurements": [],
        "gamma_measurements": [],
    }

    latest_grayscale_pass = (
        result.grayscale_passes[-1] if result.grayscale_passes else []
    )

    if session_step == "pre_grayscale":
        bucket_map["pre_measurements"] = (
            latest_grayscale_pass or result.pre_measurements
        )
    elif session_step == "luminance":
        bucket_map["lum_measurements"] = result.lum_measurements
    elif session_step == "white_balance":
        bucket_map["wb_measurements"] = result.wb_measurements
    elif session_step == "gamma":
        bucket_map["gamma_measurements"] = result.gamma_measurements
    elif session_step == "color_tuner":
        bucket_map["cms_measurements"] = result.cms_measurements
    elif session_step == "post_grayscale":
        bucket_map["post_measurements"] = (
            latest_grayscale_pass or result.post_measurements or result.pre_measurements
        )
    return bucket_map


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuneable constants
# ---------------------------------------------------------------------------

DEBOUNCE_SECONDS: float = 2.5
"""Wait this long after the last write event before importing the file."""

FILE_READ_RETRIES: int = 5
FILE_READ_RETRY_DELAY: float = 0.5
"""Retry parameters when the file is locked by ZRO (common on Windows)."""

LOCKED_FILE_RETRY_SECONDS: float = 1.0
"""Retry this quickly when the CSV is still exclusively locked by ZRO."""

_DEFAULT_GRAYSCALE_LEVELS = list(range(0, 101, 10))  # 11 levels: 0–100 in steps of 10

# ---------------------------------------------------------------------------
# Shared singleton state
# ---------------------------------------------------------------------------

# Per-client queues for SSE consumers.
_subscribers: List[queue.Queue] = []
_sub_lock = threading.Lock()

# Lock protecting the mutable singleton fields below.
_lock = threading.Lock()

_observer: Optional[Observer] = None
_handler: Optional["_ZROHandler"] = None
_watching_path: Optional[str] = None
_last_import: Optional[Dict] = None
_watcher_error: Optional[str] = None
_last_watch_event: Optional[Dict] = None
_last_attempt: Optional[Dict] = None


# ---------------------------------------------------------------------------
# Internal event handler
# ---------------------------------------------------------------------------


class _ZROHandler(FileSystemEventHandler):
    """
    Watchdog event handler that debounces CSV writes and dispatches imports.

    Each file gets its own debounce timer.  When the timer fires the file is
    imported once; subsequent fires for the same (path, mtime) pair are
    silently skipped.
    """

    def __init__(
        self,
        session_getter: Callable[[], Optional[Dict]],
        session_saver: Callable[[], None],
        measurement_deserializer: Optional[Callable[[Dict], object]] = None,
        grayscale_level_count: int = 11,
        watched_file: Optional[str] = None,
        post_import_hook: Optional[Callable[[Dict], None]] = None,
    ) -> None:
        super().__init__()
        self._session_getter = session_getter
        self._session_saver = session_saver
        self._measurement_deserializer = measurement_deserializer
        self._grayscale_level_count = grayscale_level_count
        self._watched_file = os.path.abspath(watched_file) if watched_file else None
        self._post_import_hook = post_import_hook

        # path → timer
        self._timers: Dict[str, threading.Timer] = {}
        # path → mtime already imported
        self._imported: Dict[str, float] = {}
        self._timer_lock = threading.Lock()
        self._stopped = False

    # ── watchdog callbacks ────────────────────────────────────────────────

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule(event.src_path)

    # ── internal helpers ─────────────────────────────────────────────────

    def _schedule(self, src_path: str) -> None:
        """(Re-)schedule a debounced import for *src_path*."""
        if not src_path.lower().endswith(".csv"):
            return
        if self._watched_file and os.path.abspath(src_path) != self._watched_file:
            return

        with _lock:
            global _last_watch_event
            _last_watch_event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "file": os.path.basename(src_path),
                "path": os.path.abspath(src_path),
                "event": "modified",
            }

        self._schedule_timer(src_path, DEBOUNCE_SECONDS)

    def _schedule_timer(self, src_path: str, delay_seconds: float) -> None:
        """(Re-)schedule an import timer for *src_path* after *delay_seconds*."""
        with self._timer_lock:
            if self._stopped:
                return
            # Cancel any pending timer for this path.
            existing = self._timers.get(src_path)
            if existing is not None:
                existing.cancel()

            timer = threading.Timer(delay_seconds, self._import_file, args=(src_path,))
            timer.daemon = True
            self._timers[src_path] = timer
            timer.start()

    def stop(self) -> None:
        """Cancel any pending import timers."""
        with self._timer_lock:
            self._stopped = True
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    def _import_file(self, src_path: str) -> None:
        """Called by the debounce timer — run the actual import."""
        global _last_import, _watcher_error, _last_attempt

        if self._stopped:
            return

        with self._timer_lock:
            self._timers.pop(src_path, None)

        # ── mtime dedup ───────────────────────────────────────────────────
        try:
            mtime = os.path.getmtime(src_path)
        except OSError:
            # File vanished; ignore.
            with _lock:
                _last_attempt = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "file": os.path.basename(src_path),
                    "path": os.path.abspath(src_path),
                    "status": "missing",
                    "detail": "File disappeared before it could be imported.",
                }
            return

        with self._timer_lock:
            if self._imported.get(src_path) == mtime:
                logger.debug("Skipping already-imported %s (mtime unchanged)", src_path)
                with _lock:
                    _last_attempt = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "file": os.path.basename(src_path),
                        "path": os.path.abspath(src_path),
                        "status": "skipped",
                        "detail": "File mtime is unchanged; duplicate import suppressed.",
                    }
                return

        with _lock:
            _last_attempt = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "file": os.path.basename(src_path),
                "path": os.path.abspath(src_path),
                "status": "reading",
                "detail": "Attempting to read and parse watched CSV.",
            }

        # ── parse ─────────────────────────────────────────────────────────
        # Read bytes first so we can retry if ZRO still holds a write lock.
        try:
            contents = _read_bytes_with_retry(src_path)
            result: ZROImportResult = parse_zro_csv(contents)
        except PermissionError as exc:
            msg = f"Waiting for ZRO to release lock on {src_path}: {exc}"
            logger.info(msg)
            with _lock:
                _watcher_error = msg
                _last_attempt = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "file": os.path.basename(src_path),
                    "path": os.path.abspath(src_path),
                    "status": "locked",
                    "detail": str(exc),
                }
            self._schedule_timer(src_path, LOCKED_FILE_RETRY_SECONDS)
            return
        except Exception as exc:
            msg = f"ZRO parse error for {src_path}: {exc}"
            logger.warning(msg)
            with _lock:
                _watcher_error = msg
                _last_attempt = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "file": os.path.basename(src_path),
                    "path": os.path.abspath(src_path),
                    "status": "error",
                    "detail": str(exc),
                }
            return

        if result.total_rows == 0:
            with _lock:
                _watcher_error = f"No valid rows in {src_path}"
                _last_attempt = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "file": os.path.basename(src_path),
                    "path": os.path.abspath(src_path),
                    "status": "empty",
                    "detail": "No valid measurement rows were found in the watched CSV.",
                }
            return

        # ── session lookup ────────────────────────────────────────────────
        session = self._session_getter()
        if session is None:
            with _lock:
                _watcher_error = "No active session — import skipped"
                _last_attempt = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "file": os.path.basename(src_path),
                    "path": os.path.abspath(src_path),
                    "status": "no_session",
                    "detail": "No active calibration session was available for import.",
                }
            return

        # ── merge ─────────────────────────────────────────────────────────
        bucket_map = _bucket_map_for_session_step(result, session.get("step"))
        step_warnings = _warnings_for_session_step(result, session.get("step"))

        counts: Dict[str, int] = {}
        deser = self._measurement_deserializer
        for key, meas_dicts in bucket_map.items():
            if key in ("pre_measurements", "post_measurements"):
                candidate_measurements = [
                    deser(d) if deser is not None else d for d in meas_dicts
                ]
                existing_signatures = [
                    _measurement_signature(measurement)
                    for measurement in session.setdefault(key, [])
                ]
                candidate_signatures = [
                    _measurement_signature(measurement)
                    for measurement in candidate_measurements
                ]
                if (
                    candidate_measurements
                    and candidate_signatures != existing_signatures
                ):
                    session[key] = candidate_measurements
                    counts[key] = len(candidate_measurements)
                continue

            existing_signatures = {
                _measurement_signature(measurement)
                for measurement in session.setdefault(key, [])
            }
            appended = 0
            for d in meas_dicts:
                measurement = deser(d) if deser is not None else d
                signature = _measurement_signature(measurement)
                if signature in existing_signatures:
                    continue
                session[key].append(measurement)
                existing_signatures.add(signature)
                appended += 1
            if appended:
                counts[key] = appended

        if not counts:
            with _lock:
                _last_attempt = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "file": os.path.basename(src_path),
                    "path": os.path.abspath(src_path),
                    "status": "no_new_rows",
                    "detail": "Watched CSV changed, but it only contained rows already imported for this session.",
                }
                _watcher_error = None
            with self._timer_lock:
                self._imported[src_path] = mtime
            return

        # Update peak luminance if white was measured.
        if counts.get("lum_measurements"):
            latest_lum = session["lum_measurements"][-1]
            latest_y = latest_lum["Y"] if isinstance(latest_lum, dict) else latest_lum.Y
            session["peak_luminance"] = round(latest_y, 2)

        now = datetime.now(timezone.utc).isoformat()

        # Record import metadata on the session.
        measurement_timestamps = []
        for measurements in bucket_map.values():
            for measurement in measurements:
                ts = measurement.get("timestamp")
                if ts:
                    measurement_timestamps.append(ts)
        import_meta: Dict = {
            "filename": os.path.basename(src_path),
            "timestamp": now,
            "total_rows": result.total_rows,
            "sessions_detected": result.sessions_detected,
            "colour_sessions": result.colour_sessions,
            "grayscale_sessions": result.grayscale_sessions,
            "session_breaks": result.session_breaks,
            "abl_warnings": step_warnings,
            "unknown_rows": result.unknown_rows,
            "buckets_populated": counts,
            "measurement_start": min(measurement_timestamps)
            if measurement_timestamps
            else None,
            "measurement_end": max(measurement_timestamps)
            if measurement_timestamps
            else None,
            "auto_import": True,
        }
        session.setdefault("zro_imports", []).append(import_meta)

        # Save.
        try:
            self._session_saver()
        except Exception as exc:
            logger.warning("Could not save session after auto-import: %s", exc)

        # Mark imported.
        with self._timer_lock:
            self._imported[src_path] = mtime

        # ── emit SSE event ────────────────────────────────────────────────
        event_payload = {
            "type": "import",
            "file": os.path.basename(src_path),
            "timestamp": now,
            "buckets": counts,
            "abl_warnings": step_warnings,
            "total_rows": result.total_rows,
            "measurement_start": import_meta["measurement_start"],
            "measurement_end": import_meta["measurement_end"],
        }

        with _lock:
            _last_import = {
                "file": os.path.basename(src_path),
                "timestamp": now,
                "buckets": counts,
                "abl_warnings": step_warnings,
                "measurement_start": import_meta["measurement_start"],
                "measurement_end": import_meta["measurement_end"],
            }
            _last_attempt = {
                "timestamp": now,
                "file": os.path.basename(src_path),
                "path": os.path.abspath(src_path),
                "status": "imported",
                "detail": f"Imported {result.total_rows} rows into {len(counts)} bucket(s).",
            }
            _watcher_error = None

        _broadcast_event(event_payload)
        logger.info(
            "Auto-imported %s (%d rows, %d buckets)",
            src_path,
            result.total_rows,
            len(counts),
        )

        if self._post_import_hook is not None:
            try:
                self._post_import_hook(session)
            except Exception as exc:
                logger.warning("post_import_hook raised: %s", exc)


def _broadcast_event(payload: Dict) -> None:
    """Send an import event to every active SSE subscriber."""
    with _sub_lock:
        subscribers = list(_subscribers)
    for subscriber in subscribers:
        subscriber.put(payload)


def _measurement_signature(measurement: object) -> tuple:
    """Stable identity for deduplicating repeated rows from append-only logs."""
    if isinstance(measurement, dict):
        timestamp = measurement.get("timestamp")
        label = measurement.get("label")
        stimulus_rgb = tuple(measurement.get("stimulus_rgb", []))
        Y = measurement.get("Y")
        x = measurement.get("x")
        y = measurement.get("y")
    else:
        timestamp = getattr(measurement, "timestamp", None)
        label = getattr(measurement, "label", None)
        stimulus_rgb = tuple(getattr(measurement, "stimulus_rgb", ()))
        Y = getattr(measurement, "Y", None)
        x = getattr(measurement, "x", None)
        y = getattr(measurement, "y", None)
    return (timestamp, label, stimulus_rgb, Y, x, y)


def _warnings_for_session_step(
    result: ZROImportResult, session_step: Optional[str]
) -> List[str]:
    if session_step in ("pre_grayscale", "post_grayscale"):
        return (
            result.grayscale_pass_warnings[-1] if result.grayscale_pass_warnings else []
        )
    return result.abl_warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_watching(
    path: str,
    session_getter: Callable[[], Optional[Dict]],
    session_saver: Callable[[], None],
    measurement_deserializer: Optional[Callable[[Dict], object]] = None,
    grayscale_level_count: int = 11,
    post_import_hook: Optional[Callable[[Dict], None]] = None,
) -> None:
    """
    Start watching *path* for new or modified ``.csv`` files.

    Only one watcher can be active at a time.  Calling this while a watcher
    is already running first stops the old one.

    Parameters
    ----------
    path:
        Absolute path to the directory to watch.
    session_getter:
        Zero-argument callable that returns the current session dict (or
        ``None`` if no session is active).
    session_saver:
        Zero-argument callable that persists the current session to disk.
    measurement_deserializer:
        Optional callable that converts a raw measurement dict into the
        object type stored in session measurement lists (e.g.
        ``_deserialize_measurement`` from server.py).  When ``None`` the
        raw dicts are stored directly.
    grayscale_level_count:
        Number of grayscale levels required for a complete pre-cal ramp
        (used for auto-advancing the session step).  Defaults to 11.
    """
    global _observer, _handler, _watching_path, _watcher_error

    stop_watching()

    path = os.path.abspath(str(path))  # normalise pathlib.Path → str
    watched_file: Optional[str] = None

    if os.path.isdir(path):
        watch_dir = path
    elif path.lower().endswith(".csv") and os.path.isdir(os.path.dirname(path) or "."):
        watch_dir = os.path.dirname(path)
        watched_file = path
    else:
        raise ValueError(
            f"Watch path does not exist or is not a directory/CSV file: {path!r}"
        )

    handler = _ZROHandler(
        session_getter,
        session_saver,
        measurement_deserializer=measurement_deserializer,
        grayscale_level_count=grayscale_level_count,
        watched_file=watched_file,
        post_import_hook=post_import_hook,
    )
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=False)
    observer.daemon = True
    observer.start()

    with _lock:
        _observer = observer
        _handler = handler
        _watching_path = path
        _watcher_error = None

    logger.info("File watcher started on %s", path)


def stop_watching() -> None:
    """Stop the active file watcher, if any."""
    global _observer, _handler, _watching_path

    with _lock:
        obs = _observer
        handler = _handler
        _observer = None
        _handler = None
        _watching_path = None

    if obs is not None and obs.is_alive():
        try:
            if handler is not None:
                handler.stop()
            obs.stop()
            obs.join(timeout=5.0)
        except Exception as exc:  # pragma: no cover
            logger.warning("Error stopping observer: %s", exc)
        logger.info("File watcher stopped")


def get_status() -> Dict:
    """
    Return the current watcher status.

    Returns
    -------
    dict with keys:
        ``watching`` (bool), ``path`` (str | None),
        ``last_import`` (dict | None), ``error`` (str | None)
    """
    diagnostics: Dict[str, Optional[object]] = {
        "watched_file": None,
        "watched_file_exists": None,
        "watched_file_size_bytes": None,
        "watched_file_mtime": None,
        "pending_files": 0,
        "last_event": None,
        "last_attempt": None,
    }

    handler = _handler
    if handler is not None:
        diagnostics["pending_files"] = len(handler._timers)
        if handler._watched_file:
            watched_file = handler._watched_file
            diagnostics["watched_file"] = watched_file
            diagnostics["watched_file_exists"] = os.path.exists(watched_file)
            if os.path.exists(watched_file):
                try:
                    stat = os.stat(watched_file)
                    diagnostics["watched_file_size_bytes"] = stat.st_size
                    diagnostics["watched_file_mtime"] = datetime.fromtimestamp(
                        stat.st_mtime
                    ).isoformat()
                except OSError:
                    pass

    with _lock:
        return {
            "watching": _observer is not None and _observer.is_alive(),
            "path": str(_watching_path) if _watching_path is not None else None,
            "last_import": _last_import,
            "error": _watcher_error,
            "diagnostics": {
                **diagnostics,
                "last_event": _last_watch_event,
                "last_attempt": _last_attempt,
            },
        }


def subscribe() -> queue.Queue:
    """Create and register a per-client SSE queue."""
    q: queue.Queue = queue.Queue()
    with _sub_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    """Remove a per-client SSE queue."""
    with _sub_lock:
        if q in _subscribers:
            _subscribers.remove(q)
