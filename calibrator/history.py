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
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


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
    """Append a completed calibration session to the per-TV history file.

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

    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")

    # Write baseline on the very first session only.
    baseline = _baseline_path(tv_key)
    if not baseline.exists():
        with open(baseline, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, indent=2)


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

    # Rewrite the entire file with updated entry
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")
    except OSError:
        return False

    return True


def update_baseline(tv_key: str, metrics: Dict[str, Any]) -> bool:
    """Update the baseline entry with computed metrics.

    Args:
        tv_key: TV profile key
        metrics: Dict of metric keys and values to merge into the baseline

    Returns:
        True if baseline was found and updated, False otherwise.
    """
    path = _baseline_path(tv_key)
    if not path.exists():
        return False

    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    baseline.update(metrics)

    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(baseline, fh, indent=2)
    except OSError:
        return False

    return True


def history_summary(tv_key: str) -> Dict[str, Any]:
    """Return a lightweight summary dict for API exposure.

    Keys: session_count, latest_date, latest_post_de, baseline_post_de.
    """
    history = load_history(tv_key)
    baseline = load_baseline(tv_key)
    if not history:
        return {"session_count": 0, "latest_date": None, "latest_post_de": None, "baseline_post_de": None}
    latest = history[0]
    return {
        "session_count": len(history),
        "latest_date": latest.get("date"),
        "latest_post_de": latest.get("post_grayscale_avg_de"),
        "baseline_post_de": baseline.get("post_grayscale_avg_de") if baseline else None,
    }
