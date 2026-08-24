"""Per-display calibration history store.

Persists completed session outcomes to .calibration-history/{tv_key}/sessions.jsonl
so the LLM can receive prior-session context and detect drift/aging trends.

Storage layout::

    .calibration-history/
        {tv_key}/
            sessions.jsonl      # one JSON line per completed session (newest at bottom)
            baseline.json       # copy of the very first session (reference)

The directory is configurable via the TVCAL_HISTORY_DIR environment variable.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Process-wide lock serializing history read-modify-write sequences
# (#640). FastAPI sync endpoints run in separate threadpool threads; without
# this lock, two concurrent record_session() calls for the same tv_key read
# the same entries list, each append its entry, and each rewrite the full
# file — last writer wins, one session's entry silently vanishes.
_HISTORY_LOCK = threading.Lock()


def _atomic_write_lines(path: Path, lines: List[str]) -> None:
    """Atomically write lines to *path* via tmp + os.replace (#640).

    Mirrors server._save_prefs / SessionStore.save_session: a crash or
    OSError mid-write leaves the previously-persisted file intact rather
    than leaving a truncated/corrupt sessions.jsonl on disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _history_root() -> Path:
    return Path(os.getenv("TVCAL_HISTORY_DIR", ".calibration-history"))


def _safe_key(tv_key: str) -> str:
    """Sanitise a TV key to a safe directory name."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in tv_key)


def _sessions_path(tv_key: str) -> Path:
    return _history_root() / _safe_key(tv_key) / "sessions.jsonl"


def _baseline_path(tv_key: str) -> Path:
    return _history_root() / _safe_key(tv_key) / "baseline.json"


def record_session(
    tv_key: str,
    session_id: str,
    mode: str,
    report: Dict[str, Any],
    accepted_compromises: Optional[List[str]] = None,
    wb_final: Optional[Dict[str, Any]] = None,
    cms_final: Optional[Dict[str, Any]] = None,
) -> None:
    """Upsert a completed calibration session to the per-TV history file.

    Idempotent by *session_id*: if an entry with the same ``session_id``
    already exists it is updated in-place; otherwise a new line is appended.
    This prevents duplicate entries on server restart or report re-fetch.

    Args:
        tv_key:               TV profile key (e.g. "u8g").
        session_id:           Unique session identifier.
        mode:                 Calibration mode ("SDR", "HDR10", "Dolby Vision").
        report:               Full report payload dict (from calibrator.reports.report_payload).
        accepted_compromises: List of compromise tags accepted this session
                              (e.g. ["cyan_75pct_sat_relaxed"]).
        wb_final:             Final white-balance gain/offset values applied, if available.
        cms_final:            Final CMS hue/sat/brightness values applied, if available.
    """
    _HISTORY_LOCK.acquire()
    try:
        path = _sessions_path(tv_key)
        path.parent.mkdir(parents=True, exist_ok=True)

        entry: Dict[str, Any] = {
            "session_id": session_id,
            "date": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "pre_grayscale_avg_de": (report.get("pre_cal") or {}).get("avg_de"),
            "post_grayscale_avg_de": (report.get("post_cal") or {}).get("avg_de"),
            "gamma_avg": (report.get("gamma") or {}).get("avg_gamma"),
            "peak_luminance": report.get("peak_luminance"),
            "improvement_pct": report.get("improvement_pct"),
            "wb_avg_de": (report.get("white_balance") or {}).get("avg_de"),
            "cms_avg_de": (report.get("color_tuner") or {}).get("avg_de"),
            "accepted_compromises": accepted_compromises or [],
            "wb_final": wb_final or {},
            "cms_final": cms_final or {},
        }

        # Idempotent upsert: update existing entry if session_id matches, append otherwise.
        entries: List[Dict[str, Any]] = []
        found = False
        if path.exists():
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                existing = json.loads(line)
                                if existing.get("session_id") == session_id:
                                    existing.update(entry)
                                    found = True
                                entries.append(existing)
                            except json.JSONDecodeError:
                                logger.warning("Skipped corrupted JSON line in %s", path)
                                pass
            except OSError:
                pass

        if not found:
            entries.append(entry)

        # Atomic write via tmp + os.replace so a mid-write crash never
        # truncates the history file (#640).
        _atomic_write_lines(path, [json.dumps(e) for e in entries])

        # Write baseline on the very first session only.
        baseline = _baseline_path(tv_key)
        if not baseline.exists():
            _atomic_write_lines(baseline, [json.dumps(entry, indent=2)])
    finally:
        _HISTORY_LOCK.release()


def load_history(tv_key: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Return up to *limit* sessions for this TV, most recent first.

    Returns an empty list if no history exists yet.
    """
    path = _sessions_path(tv_key)
    if not path.exists():
        return []

    entries: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return []

    # Most recent first.
    return list(reversed(entries))[:limit]


def count_sessions(tv_key: str) -> int:
    """Return the total number of recorded sessions for this TV.

    Unlike load_history(), this counts every line in sessions.jsonl and is
    not capped by the display limit. A line that fails to parse as JSON
    (e.g. a partial line from a concurrent in-progress append) is skipped
    rather than counted, so this is a conservative lower bound if
    record_session() is writing at the exact moment this is called.
    """
    path = _sessions_path(tv_key)
    if not path.exists():
        return 0

    count = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        json.loads(line)
                        count += 1
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return 0

    return count


def load_baseline(tv_key: str) -> Optional[Dict[str, Any]]:
    """Return the first-ever calibration baseline for this TV, or None."""
    path = _baseline_path(tv_key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def update_history_entry(tv_key: str, session_id: str, metrics: Dict[str, Any]) -> bool:
    """Update an existing history entry with computed metrics.

    Args:
        tv_key: TV profile key
        session_id: Session identifier to update
        metrics: Dict of metric keys and values to merge into the entry

    Returns:
        True if entry was found and updated, False otherwise.
    """
    _HISTORY_LOCK.acquire()
    try:
        path = _sessions_path(tv_key)
        if not path.exists():
            return False

        entries: List[Dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            return False

        updated = False
        for entry in entries:
            if entry.get("session_id") == session_id:
                entry.update(metrics)
                updated = True

        if not updated:
            return False

        # Rewrite atomically (tmp + os.replace) under the history lock (#640).
        _atomic_write_lines(path, [json.dumps(entry) for entry in entries])
    finally:
        _HISTORY_LOCK.release()

    return True


def update_baseline(tv_key: str, metrics: Dict[str, Any]) -> bool:
    """Update the baseline entry with computed metrics.

    Args:
        tv_key: TV profile key
        metrics: Dict of metric keys and values to merge into the baseline

    Returns:
        True if baseline was found and updated, False otherwise.
    """
    _HISTORY_LOCK.acquire()
    try:
        path = _baseline_path(tv_key)
        if not path.exists():
            return False

        try:
            baseline = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False

        baseline.update(metrics)

        try:
            # Atomic write via tmp + os.replace (#640).
            _atomic_write_lines(path, [json.dumps(baseline, indent=2)])
        except OSError:
            return False
    finally:
        _HISTORY_LOCK.release()

    return True


def history_summary(tv_key: str) -> Dict[str, Any]:
    """Return a lightweight summary dict for API exposure.

    Keys: session_count, latest_date, latest_post_de, baseline_post_de.
    session_count is the total number of recorded sessions (via
    count_sessions), not capped by load_history()'s display limit.
    """
    history = load_history(tv_key)
    baseline = load_baseline(tv_key)
    if not history:
        return {"session_count": 0, "latest_date": None, "latest_post_de": None, "baseline_post_de": None}
    latest = history[0]
    return {
        "session_count": count_sessions(tv_key),
        "latest_date": latest.get("date"),
        "latest_post_de": latest.get("post_grayscale_avg_de"),
        "baseline_post_de": baseline.get("post_grayscale_avg_de") if baseline else None,
    }
