#!/usr/bin/env python3
"""
ZRO Calibration Helper — web API backend.

Run with:
    uvicorn server:app --host 0.0.0.0 --port 8000
    # or: python server.py
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
from contextlib import asynccontextmanager, suppress as context_suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB limit for CSV uploads
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from calcore.analysis import analyze as _calcore_analyze
from calcore.gamut import (
    assess_gamut_constraints as _assess_gamut_constraints,
    format_gamut_diagnosis as _format_gamut_diagnosis,
    gamut_diagnosis_to_dict as _gamut_diagnosis_to_dict,
)
from calcore.patch_planner import MAX_RGB as _MAX_RGB
from calcore.llm import (
    build_history_block as _build_history_block,
    call_llm as _call_llm,
    parse_adjustment_plan as _parse_adjustment_plan,
    predict_initial_settings as _predict_initial_settings,
    predict_next_settings as _predict_next_settings,
    probe_llm as _probe_llm,
    query_delta_summary as _query_delta_summary,
    query_gamut_advice as _query_gamut_advice,
    query_next_patch_strategy as _query_next_patch_strategy,
    query_patch_optimization as _query_patch_optimization,
    query_remediation as _query_remediation,
    query_pass_decision as _query_pass_decision,
)
from calcore.models import AnalysisConfig, LLMConfig, Patch, TVSettings
from calcore.phase import determine_phase as _determine_phase
from calibrator import TV_PROFILES, get_tv_profile as _get_tv_profile
from calibrator.history import (
    history_summary as _history_summary,
    load_baseline as _load_baseline,
    load_history as _load_history,
    record_session as _record_session,
    update_baseline as _update_baseline,
    update_history_entry as _update_history_entry,
)
from calibrator.file_watcher import (
    get_status as _fw_status,
    start_watching as _fw_start,
    stop_watching as _fw_stop,
    subscribe as _fw_subscribe,
    unsubscribe as _fw_unsubscribe,
)
from calibrator.guidance import (
    cms_hints as _cms_hints,
    target_nits_for_colour as _target_nits_for_colour,
    target_xy_for_colour as _target_xy_for_colour,
    wb_control_plan as _wb_control_plan,
    wb_hints as _wb_hints,
    wb_recommendations as _wb_recommendations,
)
from calibrator.quality import QG_LUMINANCE_PCT, step_quality as _step_quality
from calibrator.reports import (
    comparison_payload as _comparison_payload,
    render_comparison_html as _render_comparison_html,
    render_comparison_pdf as _render_comparison_pdf,
    render_report_html as _render_report_html,
    render_report_pdf as _render_report_pdf,
    report_payload as _report_payload,
)
from calibrator.osd import translate_from_adjustment_plan as _osd_translate
from calibrator.session import (
    CMS_PATCHES,
    cms_patches as _cms_patches,
    cv as _cv,
    MODE_OPTIONS,
    PATTERN_GENERATOR_OPTIONS,
    SDR_AMBIENT_GUIDE,
    SessionStore,
    deserialize_measurement as _deserialize_measurement,
    gamma_levels_for_session as _gamma_levels_for_session,
    gamma_pass_complete as _gamma_pass_complete,
    grayscale_levels_for_ramp as _grayscale_levels_for_ramp,
    latest_grayscale_pass as _latest_grayscale_pass,
    latest_wb_measurements as _latest_wb_measurements,
    m_to_dict as _m_to_dict,
    now as _now,
    recommended_code_scale as _recommended_code_scale,
    session_view as _session_view,
    validate_peak_luminance as _validate_peak_luminance,
    zro_step_instructions as _zro_step_instructions,
)
from calibrator.utils import get_all_measurements as _get_all_measurements
import calibrator.adb_control as _adb
from calibrator.autocal import ControllerConfig
from calibrator.autocal_apply import (
    AdbApplyTarget,
    ApplyTarget,
    FallbackApplyTarget,
    MeasurementSource,
    ManualApplyTarget,
)
from calibrator.autocal_loop import AutocalLoop

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory log buffer — streams recent log records to the frontend
# ---------------------------------------------------------------------------


class _LogBuffer(logging.Handler):
    """Circular buffer of recent log records with SSE subscriber support."""

    MAX_LINES = 500

    def __init__(self) -> None:
        super().__init__()
        self._lines: collections.deque = collections.deque(maxlen=self.MAX_LINES)
        self._subscribers: List[queue.Queue] = []
        self._sub_lock = threading.Lock()
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s — %(message)s", "%H:%M:%S"
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        self._lines.append(line)
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(line)
            except queue.Full:
                pass

    def snapshot(self) -> List[str]:
        return list(self._lines)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass


_log_buffer = _LogBuffer()
_log_buffer.setLevel(logging.DEBUG)
logging.getLogger().addHandler(_log_buffer)
logging.getLogger().setLevel(logging.DEBUG)


def _validate_startup_config() -> List[str]:
    """Validate required config paths and settings at startup. Returns list of warnings."""
    warnings: List[str] = []

    if not SESSION_STORE_DIR.exists():
        try:
            SESSION_STORE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            warnings.append(
                f"Cannot create session store directory {SESSION_STORE_DIR}: {exc}"
            )

    if not os.access(SESSION_STORE_DIR, os.W_OK):
        warnings.append(f"Session store directory {SESSION_STORE_DIR} is not writable")

    if _PREFS_PATH.exists() and not os.access(_PREFS_PATH, os.W_OK):
        warnings.append(f"Prefs file {_PREFS_PATH} exists but is not writable")

    dogegen_path = os.getenv("DOGEGEN_PATH", "").strip()
    if dogegen_path and not Path(dogegen_path).exists():
        warnings.append(f"DOGEGEN_PATH points to non-existent file: {dogegen_path}")

    return warnings


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_prefs()
    startup_warnings = _validate_startup_config()
    for w in startup_warnings:
        logger.warning(f"Startup validation: {w}")
    if startup_warnings:
        logger.info(
            f"Server started with {len(startup_warnings)} configuration warning(s)"
        )
    else:
        logger.info("Server started with all validations passing")
    cleanup_task = asyncio.create_task(_session_cleanup_loop())
    yield
    cleanup_task.cancel()
    with context_suppress(asyncio.CancelledError):
        await cleanup_task


async def _session_cleanup_loop() -> None:
    """Periodically evict sessions that have exceeded SESSION_TTL."""
    while True:
        await asyncio.sleep(3600)
        try:
            expired = store.evict_expired_sessions()
            if expired:
                logger.info("Evicted %d expired session(s)", len(expired))
        except Exception:
            logger.exception("Error during session cleanup")


app = FastAPI(title="ZRO Calibration Helper", lifespan=lifespan)

_cors_origins_raw = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://localhost:8000").strip()
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()] if _cors_origins_raw else ["http://localhost:5173", "http://localhost:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


SESSION_STORE_DIR = Path(__file__).parent / ".sessions"
SESSION_TTL = timedelta(days=7)


class _WatchedSessionState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sid: Optional[str] = None

    def get(self) -> Optional[str]:
        with self._lock:
            return self._sid

    def set(self, sid: Optional[str]) -> None:
        with self._lock:
            self._sid = sid


class _DogegenState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.proc: Optional[subprocess.Popen] = None
        self.started_at: Optional[datetime] = None
        self.launch_cmd: List[str] = []
        self.last_error: Optional[str] = None

    def is_running(self) -> bool:
        with self._lock:
            if self.proc is None:
                return False
            if self.proc.poll() is None:
                return True
            self.proc = None
            self.started_at = None
            return False

    def get_started_at(self) -> Optional[datetime]:
        with self._lock:
            return self.started_at

    def get_proc_pid(self) -> Optional[int]:
        with self._lock:
            return self.proc.pid if self.proc is not None else None

    def get_last_error(self) -> Optional[str]:
        with self._lock:
            return self.last_error

    def get_launch_cmd(self) -> List[str]:
        with self._lock:
            return list(self.launch_cmd)

    def set_started(self, proc: subprocess.Popen, started_at: datetime, cmd: List[str]) -> None:
        with self._lock:
            self.proc = proc
            self.started_at = started_at
            self.launch_cmd = list(cmd)
            self.last_error = None

    def set_failed(self, cmd: List[str], error: str) -> None:
        with self._lock:
            self.proc = None
            self.started_at = None
            self.launch_cmd = list(cmd)
            self.last_error = error

    def set_last_error(self, error: str) -> None:
        with self._lock:
            self.last_error = error

    def terminate(self) -> None:
        with self._lock:
            if self.proc is not None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=3)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
                finally:
                    self.proc = None
                    self.started_at = None


class _ZroBridgeState:
    def __init__(self, url: str) -> None:
        self._lock = threading.Lock()
        self._url = url

    def get(self) -> str:
        with self._lock:
            return self._url

    def set(self, url: str) -> None:
        with self._lock:
            self._url = url


_watched_session = _WatchedSessionState()
_dogegen_state = _DogegenState()
_zro_bridge = _ZroBridgeState(os.getenv("ZRO_BRIDGE_URL", "http://localhost:7070").rstrip("/"))
_DOGEGEN_READY_DELAY_SECONDS = 2.0
_DOGEGEN_DEFAULT_WINDOW_PCT = 10
_DOGEGEN_DEFAULT_MAXCLL = 1000
_dogegen_config: Dict[str, Any] = {
    "path": os.getenv("DOGEGEN_PATH", "").strip(),
    "resolve_host": os.getenv("DOGEGEN_RESOLVE_HOST", "").strip(),
    "window_pct": int(os.getenv("DOGEGEN_WINDOW_PCT", str(_DOGEGEN_DEFAULT_WINDOW_PCT))),
    "maxcll": int(os.getenv("DOGEGEN_MAXCLL", str(_DOGEGEN_DEFAULT_MAXCLL))),
}

# Per-session LLM SSE subscriber queues
_llm_queues: Dict[str, List[queue.Queue]] = {}
_llm_queues_lock = threading.Lock()

# Per-session autocal run state + SSE subscriber queues
_autocal_loops: Dict[str, AutocalLoop] = {}
_autocal_loops_lock = threading.Lock()
_autocal_queues: Dict[str, List[queue.Queue]] = {}
_autocal_queues_lock = threading.Lock()
# Full history of the most recently completed run per session, for
# GET /api/session/{sid}/autocal/history — cleared only by a new run starting.
_autocal_last_run: Dict[str, Dict[str, Any]] = {}
_autocal_history_lock = threading.Lock()
# Per-session gate the manual/ADB-fallback apply path blocks on until the user
# confirms (via POST .../autocal/confirm) that they've made the instructed
# change on the TV — see AutocalLoop.confirm_step (roadmap Item 1e/1f).
_autocal_confirm_events: Dict[str, threading.Event] = {}
_autocal_confirm_lock = threading.Lock()
_AUTOCAL_BRIDGE_TIMEOUT = 30.0
_AUTOCAL_POLL_INTERVAL = 0.5

store = SessionStore(
    session_dir_getter=lambda: SESSION_STORE_DIR,
    ttl_getter=lambda: SESSION_TTL,
    watched_session_id_getter=lambda: _watched_session.get(),
)
store.load_sessions()

_sessions = store.sessions

# ---------------------------------------------------------------------------
# Preferences — persisted to .prefs.json, loaded on startup
# ---------------------------------------------------------------------------
_PREFS_PATH = Path(__file__).parent / ".prefs.json"
_AUTOCAL_DEFAULTS: Dict[str, Any] = {
    "apply_mode": "manual",
    "damping": ControllerConfig().damping,
    "max_iterations": 8,
    "skip_stalled_controls": False,
    "bridge_timeout": _AUTOCAL_BRIDGE_TIMEOUT,
    "bridge_poll_interval": _AUTOCAL_POLL_INTERVAL,
}

_prefs: Dict[str, Any] = {
    "dogegen": {},
    "bridge_url": "",
    "watch_folder": "",
    "llm": {"endpoint": "", "model": ""},
    "session_defaults": {
        "signal_range": "full",
        "code_scale": "8bit",
        "pattern_generator": "dogegen",
    },
    "autocal": dict(_AUTOCAL_DEFAULTS),
    # Selected ArgyllCMS meter (issue #531) — port is the spotread -c listno
    # index; instrument_name is the display string from the last discovery
    # scan, kept alongside so the UI can show a selection without re-scanning.
    # correction_path (issue #535) is the CCMX/CCSS meter-correction file
    # passed through to spotread -X; colorimeters need one on wide-gamut
    # panels, spectrophotometers (i1Pro) don't.
    "argyll": {"port": "", "instrument_name": "", "correction_path": ""},
}


def _load_prefs() -> None:
    """Read .prefs.json and apply to live globals. Env vars set initial values;
    saved prefs overwrite them so the user's last UI choice always wins."""
    if not _PREFS_PATH.exists():
        return
    try:
        saved = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse .prefs.json; using defaults", exc_info=True)
        return
    for key in ("dogegen", "bridge_url", "watch_folder", "llm", "session_defaults", "argyll"):
        if key in saved:
            _prefs[key] = saved[key]
    if "autocal" in saved:
        # Merge onto defaults so a prefs file saved before a new autocal field
        # was added doesn't silently drop the field for the rest of the run.
        _prefs["autocal"] = {**_AUTOCAL_DEFAULTS, **saved["autocal"]}
    for field in ("path", "resolve_host", "window_pct", "maxcll"):
        if field in _prefs.get("dogegen", {}):
            _dogegen_config[field] = _prefs["dogegen"][field]
    if _prefs.get("bridge_url"):
        _zro_bridge.set(_prefs["bridge_url"])


def _save_prefs() -> None:
    """Snapshot current globals into _prefs and write atomically."""
    _prefs["dogegen"] = dict(_dogegen_config)
    _prefs["bridge_url"] = _zro_bridge.get()
    try:
        tmp = _PREFS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_prefs, indent=2), encoding="utf-8")
        tmp.replace(_PREFS_PATH)
    except OSError as exc:
        logger.warning("Could not save preferences to %s: %s", _PREFS_PATH, exc)
    except Exception as exc:
        logger.error("Unexpected error saving preferences: %s", exc)


_save_session = store.save_session


class CreateSessionReq(BaseModel):
    tv_key: str = "u8g"
    sdr_peak_nits: Optional[float] = None


class SelectModeReq(BaseModel):
    mode: str
    sdr_peak_nits: Optional[float] = None


class GammaWorkflowReq(BaseModel):
    workflow: str


class LightSpaceTierReq(BaseModel):
    tier: str
    ramp_steps: int


class SignalRangeReq(BaseModel):
    signal_range: str


class PatternGeneratorReq(BaseModel):
    pattern_generator: str


class CodeScaleReq(BaseModel):
    code_scale: str


class GrayscaleRampReq(BaseModel):
    ramp_steps: int


class JumpToStepReq(BaseModel):
    step_index: int


class DogegenConfigReq(BaseModel):
    path: Optional[str] = None
    resolve_host: Optional[str] = None
    window_pct: Optional[int] = None
    maxcll: Optional[int] = None


class PrefsReq(BaseModel):
    signal_range: Optional[str] = None
    code_scale: Optional[str] = None
    pattern_generator: Optional[str] = None
    llm_endpoint: Optional[str] = None
    llm_model: Optional[str] = None
    watch_folder: Optional[str] = None
    autocal_apply_mode: Optional[str] = None
    autocal_damping: Optional[float] = None
    autocal_max_iterations: Optional[int] = None
    autocal_skip_stalled_controls: Optional[bool] = None
    autocal_bridge_timeout: Optional[float] = None
    autocal_bridge_poll_interval: Optional[float] = None


class AutocalRunReq(BaseModel):
    colours: Optional[List[str]] = None
    apply_mode: Optional[str] = None
    damping: Optional[float] = None
    max_iterations: Optional[int] = None
    skip_stalled_controls: Optional[bool] = None
    bridge_timeout: Optional[float] = None
    bridge_poll_interval: Optional[float] = None
    device: Optional[str] = None


class AdbCmsSetReq(BaseModel):
    channel: str
    control: str
    value: int
    device: Optional[str] = None


class AdbCmsAdjustReq(BaseModel):
    channel: str
    control: str
    delta: int
    device: Optional[str] = None


class AdbCmsGetReq(BaseModel):
    channel: str
    control: str
    device: Optional[str] = None


class AdbPictureSetReq(BaseModel):
    control: str
    value: int
    device: Optional[str] = None


class AdbPictureGetReq(BaseModel):
    control: str
    device: Optional[str] = None


class TvSettingsReq(BaseModel):
    """Current TV hardware slider values for LLM context (#96)."""

    two_point_wb: Optional[Dict[str, int]] = None
    multipoint_wb: Optional[Dict[str, Any]] = None
    cms_sliders: Optional[Dict[str, Any]] = None


class ZroBridgeConfigBody(BaseModel):
    url: str


class ZroBridgeInstrumentBody(BaseModel):
    port: Optional[str] = None
    instrument_name: Optional[str] = None
    correction_path: Optional[str] = None


class ZroBridgeMeasureBody(BaseModel):
    url: Optional[str] = None


class WatchConfigBody(BaseModel):
    path: str
    sid: str


class WatchStartBody(BaseModel):
    path: str


class LlmConfigureReq(BaseModel):
    endpoint: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = None
    timeout: Optional[float] = None


class LlmProbeReq(BaseModel):
    endpoint: str
    model: str
    api_key: str = ""


class SuggestedPatchBody(BaseModel):
    nits: float
    r: int
    g: int
    b: int
    priority: str
    label: str = ""
    rationale: str = ""

    @field_validator("r", "g", "b", mode="before")
    @classmethod
    def clamp_rgb(cls, v: int) -> int:
        return max(0, min(int(v), _MAX_RGB))


class RunPatchesReq(BaseModel):
    patches: List[SuggestedPatchBody]


def _find_dogegen_executable() -> Optional[str]:
    configured = (_dogegen_config.get("path") or "").strip()
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.append(str(Path(__file__).parent / "tools" / "dogegen" / "Dogegen.exe"))
    on_path = shutil.which("Dogegen.exe") or shutil.which("dogegen.exe")
    if on_path:
        candidates.append(on_path)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    return None


def _managed_dogegen_is_running() -> bool:
    return _dogegen_state.is_running()


def _external_dogegen_pid() -> Optional[int]:
    try:
        if os.name == "nt":
            for image in ("Dogegen.exe", "Dogegen64.exe"):
                proc = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH", "/FI", f"IMAGENAME eq {image}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                lines = [
                    line.strip() for line in proc.stdout.splitlines() if line.strip()
                ]
                for line in lines:
                    if "No tasks are running" in line:
                        continue
                    parts = [part.strip().strip('"') for part in line.split('","')]
                    if len(parts) >= 2 and parts[0].lower().startswith("dogegen"):
                        try:
                            return int(parts[1])
                        except ValueError:
                            continue
            return None

        proc = subprocess.run(
            ["pgrep", "-f", "[Dd]ogegen"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                return int(line)
            except ValueError:
                continue
    except Exception:
        return None
    return None


def _dogegen_status_payload() -> Dict[str, Any]:
    path = _find_dogegen_executable()
    managed_running = _managed_dogegen_is_running()
    external_pid = None if managed_running else _external_dogegen_pid()
    running = managed_running or external_pid is not None
    ready = False
    ready_in_ms = 0
    if external_pid is not None:
        ready = True
    elif managed_running:
        started_at = _dogegen_state.get_started_at()
        elapsed = (
            0.0
            if started_at is None
            else (_now() - started_at).total_seconds()
        )
        ready = elapsed >= _DOGEGEN_READY_DELAY_SECONDS
        if not ready:
            ready_in_ms = max(0, int((_DOGEGEN_READY_DELAY_SECONDS - elapsed) * 1000))
    return {
        "configured": bool(path),
        "path": path,
        "running": running,
        "pid": _dogegen_state.get_proc_pid() if managed_running else external_pid,
        "managed": managed_running,
        "ready": ready,
        "ready_in_ms": ready_in_ms,
        "resolve_host": _dogegen_config.get("resolve_host") or "",
        "window_pct": int(_dogegen_config.get("window_pct") or _DOGEGEN_DEFAULT_WINDOW_PCT),
        "maxcll": int(_dogegen_config.get("maxcll") or _DOGEGEN_DEFAULT_MAXCLL),
        "last_error": _dogegen_state.get_last_error(),
        "launch_cmd": _dogegen_state.get_launch_cmd(),
    }


def _dogegen_command_for_session(session: Dict[str, Any], exe_path: str) -> List[str]:
    mode = session.get("mode")
    window_pct = int(_dogegen_config.get("window_pct") or _DOGEGEN_DEFAULT_WINDOW_PCT)
    maxcll = int(_dogegen_config.get("maxcll") or _DOGEGEN_DEFAULT_MAXCLL)
    resolve_host = (_dogegen_config.get("resolve_host") or "").strip()
    if mode == "HDR10":
        cmd = [exe_path, "mode 10_hdr", f"maxcll {maxcll}"]
        resolve_arg = (
            f"resolve_hdr {resolve_host} {window_pct}"
            if resolve_host
            else f"resolve_hdr {window_pct}"
        )
        cmd.append(resolve_arg)
        return cmd
    if mode == "SDR":
        host = resolve_host or "127.0.0.1"
        return [exe_path, f"maxcll {maxcll}", f"resolve_sdr {host}"]
    return [exe_path]


def _start_dogegen_for_session(session: Dict[str, Any]) -> Dict[str, Any]:
    with _dogegen_state._lock:
        if _managed_dogegen_is_running() or _external_dogegen_pid() is not None:
            return {"ok": True, "already_running": True, **_dogegen_status_payload()}
        exe_path = _find_dogegen_executable()
        if not exe_path:
            error = (
                "Dogegen.exe not found. Set DOGEGEN_PATH, configure it in the app, "
                "or place it at tools/dogegen/Dogegen.exe."
            )
            _dogegen_state.set_last_error(error)
            raise HTTPException(400, error)
        cmd = _dogegen_command_for_session(session, exe_path)
        try:
            kwargs: Dict[str, Any] = {"cwd": str(Path(exe_path).parent)}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = subprocess.Popen(cmd, **kwargs)
            _dogegen_state.set_started(proc, _now(), cmd)
            return {"ok": True, "already_running": False, **_dogegen_status_payload()}
        except Exception as exc:
            _dogegen_state.set_failed(cmd, str(exc))
            raise HTTPException(500, f"Failed to start Dogegen: {exc}") from exc


def _stop_dogegen() -> Dict[str, Any]:
    with _dogegen_state._lock:
        if not _managed_dogegen_is_running():
            return {"ok": True, "already_stopped": True, **_dogegen_status_payload()}
        _dogegen_state.terminate()
        return {"ok": True, "already_stopped": False, **_dogegen_status_payload()}


def _watch_status_payload() -> Dict[str, Any]:
    status = _fw_status()
    sid = _watched_session.get()
    status["session_id"] = sid
    status["session_exists"] = bool(sid and sid in _sessions)
    return status


def _llm_subscribe(sid: str) -> "queue.Queue[Dict[str, Any]]":
    q: queue.Queue = queue.Queue(maxsize=100)
    with _llm_queues_lock:
        _llm_queues.setdefault(sid, []).append(q)
    return q


def _llm_unsubscribe(sid: str, q: "queue.Queue[Dict[str, Any]]") -> None:
    with _llm_queues_lock:
        listeners = _llm_queues.get(sid, [])
        try:
            listeners.remove(q)
        except ValueError:
            pass
        if not listeners:
            _llm_queues.pop(sid, None)


def _llm_broadcast(sid: str, payload: Dict[str, Any]) -> None:
    with _llm_queues_lock:
        listeners = list(_llm_queues.get(sid, []))
    for q in listeners:
        try:
            q.put_nowait(payload)
        except queue.Full:
            logger.warning("LLM event queue full for session %s; dropping event", sid)


def _autocal_subscribe(sid: str) -> "queue.Queue[Dict[str, Any]]":
    q: queue.Queue = queue.Queue(maxsize=200)
    with _autocal_queues_lock:
        _autocal_queues.setdefault(sid, []).append(q)
    return q


def _autocal_unsubscribe(sid: str, q: "queue.Queue[Dict[str, Any]]") -> None:
    with _autocal_queues_lock:
        listeners = _autocal_queues.get(sid, [])
        try:
            listeners.remove(q)
        except ValueError:
            pass
        if not listeners:
            _autocal_queues.pop(sid, None)


def _autocal_broadcast(sid: str, payload: Dict[str, Any]) -> None:
    with _autocal_queues_lock:
        listeners = list(_autocal_queues.get(sid, []))
    for q in listeners:
        try:
            q.put_nowait(payload)
        except queue.Full:
            logger.warning("Autocal event queue full for session %s; dropping event", sid)


class _SessionCmsMeasurementSource(MeasurementSource):
    """Triggers a ZRO Bridge measurement, then waits for the watch-folder
    import pipeline to land a new CMS measurement for this colour on the
    session — the bridge itself only fires the meter, it does not return
    a parsed reading synchronously (see docs/autocal-roadmap.md Item 3's
    note on RGB-ignored `/measure/sequence` for the same limitation)."""

    def __init__(
        self,
        sid: str,
        timeout: float = _AUTOCAL_BRIDGE_TIMEOUT,
        poll_interval: float = _AUTOCAL_POLL_INTERVAL,
    ) -> None:
        self.sid = sid
        self.timeout = timeout
        self.poll_interval = poll_interval

    def measure(self, patch: Patch) -> Any:
        import time as _time

        colour = patch.label
        session = store.get(self.sid)
        seen_before = len(session.get("cms_measurements", []))
        zro_trigger()
        deadline = _time.monotonic() + self.timeout
        while _time.monotonic() < deadline:
            session = store.get(self.sid)
            cms = session.get("cms_measurements", [])
            if len(cms) > seen_before:
                candidate = cms[-1]
                candidate_colour = (candidate.label or "").replace(" 100%", "")
                if candidate_colour == colour:
                    return candidate
                seen_before = len(cms)
            _time.sleep(self.poll_interval)
        raise TimeoutError(
            f"No new {colour} measurement arrived within {self.timeout}s "
            "— check the watch folder is configured and the meter is connected."
        )


def _measurement_to_patch(m: Any) -> Patch:
    r, g, b = m.stimulus_rgb
    return Patch(
        label=m.label or f"RGB({r},{g},{b})",
        r_target=r,
        g_target=g,
        b_target=b,
        meas_xyz=(m.X, m.Y, m.Z),
        meas_yxy=(m.Y, m.x, m.y) if (m.x or m.y) else None,
        kind="grayscale" if r == g == b else "color",
    )


def _session_to_analysis_config(session: Dict[str, Any]) -> AnalysisConfig:
    """Build a calcore ``AnalysisConfig`` from a session dict.

    The ``signal_range`` field comes from the session's HDMI signal-range
    setting (set via ``POST /api/session/<sid>/signal-range`` or defaulting
    to ``"full"`` at session creation — see ``calibrator/session.py:1755``).
    Sessions created before this field was added will fall back to ``"full"``,
    which preserves the previous (incorrect for limited-range, but unchanged)
    analysis behavior.
    """
    mode = session.get("mode") or "SDR"
    calcore_mode = "hdr" if mode in ("HDR10", "Dolby Vision") else "sdr"

    target = session.get("target")
    raw_eotf = (target.eotf if target else "") or ""
    if "PQ" in raw_eotf or "2084" in raw_eotf:
        eotf = "pq"
    elif "1886" in raw_eotf:
        eotf = "bt1886"
    else:
        eotf = "pq" if calcore_mode == "hdr" else "bt1886"

    raw_gamut = (target.gamut if target else "") or ""
    if "2020" in raw_gamut:
        target_space = "bt2020"
    elif "P3" in raw_gamut or "p3" in raw_gamut:
        target_space = "p3d65"
    else:
        target_space = "bt709"

    code_max = 1023 if session.get("code_scale") == "10bit" else 255
    signal_range = session.get("signal_range", "full")

    return AnalysisConfig(
        mode=calcore_mode,
        eotf=eotf,
        target_space=target_space,
        code_max=code_max,
        signal_range=signal_range,
    )


def _run_llm_background(
    sid: str,
    patches: List[Patch],
    cfg: AnalysisConfig,
    phase: str,
    llm_cfg: LLMConfig,
    tv_key: str = "",
    session_step_history: Optional[List[Dict[str, Any]]] = None,
    tv_settings: Optional[TVSettings] = None,
) -> None:
    _parsed_ep = urlparse(llm_cfg.endpoint)
    _safe_endpoint = f"{_parsed_ep.scheme}://{_parsed_ep.hostname or ''}{_parsed_ep.path}"
    logger.info(
        "LLM run started  sid=%s phase=%s endpoint=%s model=%s",
        sid,
        phase,
        _safe_endpoint,
        llm_cfg.model,
    )
    _llm_broadcast(
        sid,
        {
            "event": "llm_start",
            "data": {
                "phase": phase,
                "timestamp": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            },
        },
    )
    try:
        summary = _calcore_analyze(patches, cfg)

        # Build history block for this TV so the LLM has prior-session context.
        history_block: Optional[str] = None
        if tv_key:
            try:
                hist = _load_history(tv_key, limit=3)
                baseline = _load_baseline(tv_key)
                if hist:
                    history_block = _build_history_block(hist, baseline)
            except Exception:
                logger.warning("Failed to load LLM history for tv_key=%s", tv_key, exc_info=True)
                pass  # history is advisory; never block the main LLM call

        # Inject TV settings schema when the TV model is known (#99)
        tv_schema: Optional[Dict[str, Any]] = None
        if tv_key:
            profile = _get_tv_profile(tv_key)
            if profile and profile.llm_schema:
                tv_schema = profile.llm_schema

        result = _call_llm(
            summary,
            cfg,
            phase,
            llm_cfg,
            history_block=history_block,
            tv_settings=tv_settings,
            tv_schema=tv_schema,
        )
        logger.info(
            "LLM run complete sid=%s phase=%s chars=%d", sid, phase, len(result or "")
        )

        # Attempt to parse structured AdjustmentPlan from the JSON response (#95)
        plan_data: Optional[Dict[str, Any]] = None
        if result:
            plan = _parse_adjustment_plan(result)
            if plan is not None:
                from dataclasses import asdict as _asdict

                plan_data = _asdict(plan)

        _llm_broadcast(
            sid,
            {
                "event": "llm_insight",
                "data": {
                    "phase": phase,
                    "text": result,
                    "plan": plan_data,
                    "timestamp": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            },
        )

        # Fire patch strategy query in the same background thread (low-latency path).
        if session_step_history is not None:
            budget = max(0, 51 - len(patches))  # conservative remaining budget estimate
            strategy = _query_next_patch_strategy(
                summary,
                session_step_history,
                phase,
                budget,
                llm_cfg,
            )
            if strategy is not None:
                _llm_broadcast(
                    sid,
                    {
                        "event": "patch_strategy",
                        "data": {
                            "phase": phase,
                            "focus": strategy.focus,
                            "rationale": strategy.rationale,
                            "add_patches": strategy.add_patches,
                            "skip_patches": strategy.skip_patches,
                            "confidence": strategy.confidence,
                            "auto_apply": strategy.confidence >= 0.6,
                            "timestamp": datetime.now(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                        },
                    },
                )
    except Exception as exc:
        logger.error(
            "LLM run failed  sid=%s phase=%s error=%s", sid, phase, exc, exc_info=True
        )
        _llm_broadcast(sid, {"event": "llm_error", "data": str(exc)})


def _maybe_trigger_llm(sid: str, session: Dict[str, Any]) -> None:
    """Fire a background LLM analysis if the session has LLM configured and measurements."""
    llm_cfg_dict = session.get("llm_config", {})
    endpoint = llm_cfg_dict.get("endpoint", "")
    model = llm_cfg_dict.get("model", "")
    if not (endpoint and model):
        if endpoint:
            _rep = urlparse(endpoint)
            endpoint_repr = f"{_rep.scheme}://{_rep.hostname or ''}{_rep.path}"
        else:
            endpoint_repr = ""
        logger.info(
            "LLM skip  sid=%s reason=not_configured endpoint=%r model=%r",
            sid,
            endpoint_repr,
            model,
        )
        return

    all_measurements = _get_all_measurements(session)
    if not all_measurements:
        logger.info("LLM skip  sid=%s reason=no_measurements", sid)
        return

    patches = [_measurement_to_patch(m) for m in all_measurements]
    cfg = _session_to_analysis_config(session)
    llm_cfg = LLMConfig.from_dict(llm_cfg_dict, default_timeout=30.0)
    has_key = bool(llm_cfg_dict.get("api_key"))
    logger.info(
        "LLM trigger  sid=%s step=%s patches=%d has_api_key=%s",
        sid,
        session.get("step", "baseline"),
        len(patches),
        has_key,
    )
    # Reconstruct TVSettings from session if the user supplied slider values (#96)
    tv_settings: Optional[TVSettings] = None
    raw_tv_settings = session.get("tv_settings")
    if raw_tv_settings:
        tv_settings = TVSettings(
            two_point_wb=raw_tv_settings.get("two_point_wb"),
            multipoint_wb=raw_tv_settings.get("multipoint_wb"),
            cms_sliders=raw_tv_settings.get("cms_sliders"),
        )

    threading.Thread(
        target=_run_llm_background,
        args=(sid, patches, cfg, session.get("step", "baseline"), llm_cfg),
        kwargs={
            "tv_key": session.get("tv_key", ""),
            "session_step_history": session.get("llm_step_history", []),
            "tv_settings": tv_settings,
        },
        daemon=True,
    ).start()


@app.get("/api/profiles")
def list_profiles():
    return [{"key": key, "name": profile.name} for key, profile in TV_PROFILES.items()]


@app.post("/api/session")
def create_session(req: CreateSessionReq):
    session = store.create_session(req.tv_key, req.sdr_peak_nits)
    sid = session["id"]
    sd = _prefs.get("session_defaults", {})
    if sd.get("signal_range"):
        try:
            session = store.set_signal_range(sid, sd["signal_range"])
        except Exception:
            logger.warning("Failed to apply default signal_range for sid=%s", sid, exc_info=True)
            pass
    if sd.get("code_scale"):
        try:
            session = store.set_code_scale(sid, sd["code_scale"])
        except Exception:
            logger.warning("Failed to apply default code_scale for sid=%s", sid, exc_info=True)
            pass
    if sd.get("pattern_generator"):
        try:
            session = store.set_pattern_generator(sid, sd["pattern_generator"])
        except Exception:
            logger.warning("Failed to apply default pattern_generator for sid=%s", sid, exc_info=True)
            pass
    llm = _prefs.get("llm", {})
    if llm.get("endpoint") or llm.get("model"):
        llm_cfg = session.setdefault("llm_config", {})
        if llm.get("endpoint") and not llm_cfg.get("endpoint"):
            llm_cfg["endpoint"] = llm["endpoint"]
        if llm.get("model") and not llm_cfg.get("model"):
            llm_cfg["model"] = llm["model"]
        store.save_session(sid)
        session = store.get(sid)
    return {
        **_session_view(session),
        "modes": MODE_OPTIONS,
        "sdr_ambient_guide": SDR_AMBIENT_GUIDE,
    }


@app.get("/api/session")
def get_latest_session():
    latest = store.latest_session()
    return None if latest is None else _session_view(latest)


@app.get("/api/session/{sid}")
def get_session(sid: str):
    return _session_view(store.get(sid))


@app.delete("/api/session/{sid}")
def delete_session(sid: str):
    if sid == _watched_session.get():
        _fw_stop()
        _watched_session.set(None)
    store.delete(sid)
    return {"ok": True}


@app.post("/api/session/{sid}/mode")
def select_mode(sid: str, req: SelectModeReq):
    return _session_view(store.select_mode(sid, req.mode, req.sdr_peak_nits))


@app.post("/api/session/{sid}/prepared")
def confirm_prepared(sid: str):
    return _session_view(store.confirm_prepared(sid))


@app.post("/api/session/{sid}/gamma/workflow")
def set_gamma_workflow(sid: str, req: GammaWorkflowReq):
    return _session_view(store.set_gamma_workflow(sid, req.workflow))


@app.post("/api/session/{sid}/signal-range")
def set_signal_range(sid: str, req: SignalRangeReq):
    return _session_view(store.set_signal_range(sid, req.signal_range))


@app.post("/api/session/{sid}/pattern-generator")
def set_pattern_generator(sid: str, req: PatternGeneratorReq):
    return _session_view(store.set_pattern_generator(sid, req.pattern_generator))


@app.post("/api/session/{sid}/code-scale")
def set_code_scale(sid: str, req: CodeScaleReq):
    return _session_view(store.set_code_scale(sid, req.code_scale))


@app.get("/api/dogegen/status")
def dogegen_status():
    return _dogegen_status_payload()


@app.post("/api/dogegen/config")
def dogegen_config(req: DogegenConfigReq):
    if req.path is not None:
        _dogegen_config["path"] = req.path.strip()
    if req.resolve_host is not None:
        _dogegen_config["resolve_host"] = req.resolve_host.strip()
    if req.window_pct is not None:
        if not (1 <= req.window_pct <= 100):
            raise HTTPException(400, "window_pct must be between 1 and 100")
        _dogegen_config["window_pct"] = int(req.window_pct)
    if req.maxcll is not None:
        if req.maxcll <= 0:
            raise HTTPException(400, "maxcll must be greater than 0")
        _dogegen_config["maxcll"] = int(req.maxcll)
    _save_prefs()
    return {"ok": True, **_dogegen_status_payload()}


@app.post("/api/session/{sid}/dogegen/start")
def dogegen_start_for_session(sid: str):
    return _start_dogegen_for_session(store.get(sid))


@app.post("/api/dogegen/stop")
def dogegen_stop():
    return _stop_dogegen()


@app.post("/api/session/{sid}/lightspace-tier")
def set_lightspace_tier(sid: str, req: LightSpaceTierReq):
    return _session_view(store.set_lightspace_tier(sid, req.tier, req.ramp_steps))


@app.post("/api/session/{sid}/grayscale-ramp")
def set_grayscale_ramp(sid: str, req: GrayscaleRampReq):
    return _session_view(store.set_grayscale_ramp(sid, req.ramp_steps))


@app.post("/api/session/{sid}/next")
def next_step(sid: str):
    return _session_view(store.next_step(sid, QG_LUMINANCE_PCT))


@app.post("/api/session/{sid}/prev")
def prev_step(sid: str):
    return _session_view(store.prev_step(sid))


@app.post("/api/session/{sid}/jump")
def jump_to_step(sid: str, req: JumpToStepReq):
    return _session_view(store.jump_to_step(sid, req.step_index))


@app.post("/api/session/{sid}/llm/configure")
def configure_llm(sid: str, req: LlmConfigureReq):
    session = store.get(sid)
    llm_cfg = session.setdefault(
        "llm_config",
        {
            "endpoint": "",
            "model": "",
            "api_key": "",
            "temperature": 0.2,
            "timeout": 30.0,
        },
    )

    # Update fields if provided
    if req.endpoint is not None:
        llm_cfg["endpoint"] = req.endpoint.strip()
    if req.model is not None:
        llm_cfg["model"] = req.model.strip()
    if req.api_key is not None:
        llm_cfg["api_key"] = req.api_key
    if req.temperature is not None:
        if not (0.0 <= req.temperature <= 2.0):
            raise HTTPException(400, "temperature must be between 0.0 and 2.0")
        llm_cfg["temperature"] = req.temperature
    if req.timeout is not None:
        if req.timeout <= 0:
            raise HTTPException(400, "timeout must be greater than 0")
        llm_cfg["timeout"] = req.timeout

    configured = bool(llm_cfg.get("endpoint") and llm_cfg.get("model"))

    _save_session(sid)
    if llm_cfg.get("endpoint"):
        _prefs["llm"]["endpoint"] = llm_cfg["endpoint"]
    if llm_cfg.get("model"):
        _prefs["llm"]["model"] = llm_cfg["model"]
    _save_prefs()

    return {
        "configured": configured,
        "model": llm_cfg.get("model", ""),
    }


@app.post("/api/session/{sid}/tv-settings")
def set_tv_settings(sid: str, req: TvSettingsReq):
    """Store current TV hardware slider values in the session for LLM context (#96)."""
    session = store.get(sid)
    session["tv_settings"] = {
        "two_point_wb": req.two_point_wb,
        "multipoint_wb": req.multipoint_wb,
        "cms_sliders": req.cms_sliders,
    }
    _save_session(sid)
    return {"stored": True}


@app.get("/api/session/{sid}/llm/status")
def llm_status(sid: str):
    session = store.get(sid)
    llm_cfg = session.get("llm_config", {})
    configured = bool(llm_cfg.get("endpoint") and llm_cfg.get("model"))
    reachable = False
    error = ""

    if configured:
        reachable, error = _probe_llm(llm_cfg)

    return {
        "configured": configured,
        "reachable": reachable,
        "model": llm_cfg.get("model", ""),
        "error": error,
    }


@app.post("/api/session/{sid}/llm/probe")
def probe_llm_endpoint(sid: str, req: LlmProbeReq):
    """Probe the given LLM values without persisting them to the session."""
    cfg = {
        "endpoint": req.endpoint.strip(),
        "model": req.model.strip(),
        "api_key": req.api_key,
    }
    reachable, error = _probe_llm(cfg)
    return {
        "configured": bool(cfg["endpoint"] and cfg["model"]),
        "reachable": reachable,
        "model": cfg["model"],
        "error": error,
    }


@app.get("/api/session/{sid}/llm/history-summary")
def llm_history_summary(sid: str):
    session = store.get(sid)
    tv_key = session.get("tv_key", "unknown")
    summary = _history_summary(tv_key)
    history = _load_history(tv_key, limit=3)
    has_finals = any(
        (h.get("wb_final") or h.get("cms_final")) for h in history
    )
    return {
        "tv_key": tv_key,
        "session_count": summary.get("session_count", 0),
        "latest_date": summary.get("latest_date"),
        "latest_post_de": summary.get("latest_post_de"),
        "baseline_post_de": summary.get("baseline_post_de"),
        "has_final_settings": has_finals,
    }


@app.post("/api/session/{sid}/llm/run")
def llm_run(sid: str):
    session = store.get(sid)
    llm_cfg_dict = session.get("llm_config", {})

    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        raise HTTPException(
            400, "LLM not configured; POST /api/session/{sid}/llm/configure first."
        )

    all_measurements = _get_all_measurements(session)
    if not all_measurements:
        raise HTTPException(
            400, "No measurements in session; import data before running LLM analysis."
        )

    patches = [_measurement_to_patch(m) for m in all_measurements]
    cfg = _session_to_analysis_config(session)
    llm_cfg = LLMConfig.from_dict(llm_cfg_dict, default_timeout=30.0)
    phase_str = session.get("step", "baseline")

    t = threading.Thread(
        target=_run_llm_background,
        args=(sid, patches, cfg, phase_str, llm_cfg),
        daemon=True,
    )
    t.start()

    return {"status": "running"}


@app.get("/api/session/{sid}/llm/stream")
def llm_stream(sid: str):
    store.get(sid)  # raises 404 if session doesn't exist
    ev_queue = _llm_subscribe(sid)

    def _generator():
        try:
            while True:
                try:
                    payload = ev_queue.get(timeout=20.0)
                    event_type = payload.get("event", "llm_insight")
                    data = json.dumps(payload.get("data", ""))
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            _llm_unsubscribe(sid, ev_queue)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Gamut feasibility endpoints ───────────────────────────────────────────────


@app.get("/api/session/{sid}/gamut/diagnosis")
def gamut_diagnosis(sid: str):
    """Return a per-primary gamut constraint report for the current CMS measurements."""
    session = store.get(sid)
    cms_meas = session.get("cms_measurements", [])
    if not cms_meas:
        raise HTTPException(
            400, "No CMS measurements in session; import color patches first."
        )
    cfg = _session_to_analysis_config(session)
    patches = [_measurement_to_patch(m) for m in cms_meas]
    target = session.get("target")
    white_point = target.white_point_xy if target else None
    summary = _calcore_analyze(patches, cfg, white_point)
    diagnosis = _assess_gamut_constraints(
        summary.color_rows,
        cfg.target_space,
    )
    return _gamut_diagnosis_to_dict(diagnosis)


@app.post("/api/session/{sid}/gamut/advise")
def gamut_advise(sid: str):
    """Run LLM interpretation of the gamut diagnosis and return plain-English trade-off advice."""
    session = store.get(sid)
    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        raise HTTPException(
            400, "LLM not configured; POST /api/session/{sid}/llm/configure first."
        )

    cms_meas = session.get("cms_measurements", [])
    if not cms_meas:
        raise HTTPException(
            400, "No CMS measurements in session; import color patches first."
        )

    cfg = _session_to_analysis_config(session)
    patches = [_measurement_to_patch(m) for m in cms_meas]
    target = session.get("target")
    white_point = target.white_point_xy if target else None
    summary = _calcore_analyze(patches, cfg, white_point)
    diagnosis = _assess_gamut_constraints(summary.color_rows, cfg.target_space)
    diagnosis_text = _format_gamut_diagnosis(diagnosis)

    llm_cfg = LLMConfig.from_dict(llm_cfg_dict, default_timeout=30.0)
    advice = _query_gamut_advice(diagnosis_text, cfg.target_space, llm_cfg)
    return {
        "diagnosis": _gamut_diagnosis_to_dict(diagnosis),
        "advice": advice,
    }


# ── Calibration history endpoints ─────────────────────────────────────────────


@app.get("/api/session/{sid}/history")
def session_history(sid: str):
    """Return the calibration history for this session's TV model."""
    session = store.get(sid)
    tv_key = session.get("tv_key", "")
    if not tv_key:
        return {"session_count": 0, "sessions": [], "baseline": None}
    return {
        **_history_summary(tv_key),
        "sessions": _load_history(tv_key, limit=10),
        "baseline": _load_baseline(tv_key),
    }


# ── Hardware remediation endpoint ─────────────────────────────────────────────


class OsdTranslateReq(BaseModel):
    plan: dict  # AdjustmentPlan dict from LLM


class HardwareEventReq(BaseModel):
    event_type: str
    context: dict = {}
    attempt_count: int = 1
    session_phase: str = ""


@app.post("/api/session/{sid}/osd/translate")
def osd_translate(sid: str, req: OsdTranslateReq):
    """Translate an LLM adjustment plan into step-by-step OSD navigation instructions."""
    session = store.get(sid)
    tv_key = session.get("tv_key", "")

    if not tv_key:
        raise HTTPException(400, "No TV profile selected for this session.")

    tv_profile = _get_tv_profile(tv_key)
    result = _osd_translate(req.plan, tv_profile)
    return result.to_dict()


@app.post("/api/session/{sid}/hardware/remediate")
def hardware_remediate(sid: str, req: HardwareEventReq):
    """Ask the LLM to diagnose a hardware fault and suggest recovery steps."""
    session = store.get(sid)
    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        raise HTTPException(400, "LLM not configured.")
    llm_cfg = LLMConfig.from_dict(llm_cfg_dict, default_timeout=20.0, default_temperature=0.0)
    plan = _query_remediation(
        event_type=req.event_type,
        context=req.context,
        attempt_count=req.attempt_count,
        session_phase=req.session_phase or session.get("step", ""),
        llm=llm_cfg,
    )
    if plan is None:
        raise HTTPException(
            503, "LLM remediation query failed or returned unparseable response."
        )
    return plan


# ── CSV import ────────────────────────────────────────────────────────────────


@app.post("/api/session/{sid}/import/zro")
async def import_zro_csv(sid: str, file: UploadFile = File(...)):
    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(400, f"Could not read uploaded file: {exc}") from exc

    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File too large: {len(contents)} bytes (max {_MAX_UPLOAD_BYTES})"
        )

    # Offload blocking, lock-holding disk I/O to the threadpool: this route is
    # `async def` (for `await file.read()`), so unlike plain `def` handlers it
    # is not threadpooled automatically and would otherwise stall the event
    # loop for the duration of the import (#504).
    session, import_meta = await run_in_threadpool(
        store.import_zro_bytes, sid, file.filename, contents
    )
    _maybe_trigger_llm(sid, session)
    return {"session": _session_view(session), "import_summary": import_meta}


_ALLOWED_FORMATS = frozenset({"xyY", "XYZ"})

_FORMAT_NORMALIZE = {
    "xyy": "xyY",
    "xyz": "XYZ",
}


@app.post("/api/session/{sid}/import/generic")
async def import_generic_csv(
    sid: str,
    file: UploadFile = File(...),
    format_: str = Query("xyY", alias="format"),
):
    fmt = _FORMAT_NORMALIZE.get(format_.strip().lower(), format_.strip())
    if fmt not in _ALLOWED_FORMATS:
        raise HTTPException(
            400,
            f"Invalid format '{format_}'; must be 'xyY' or 'XYZ'",
        )

    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(400, f"Could not read uploaded file: {exc}") from exc

    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File too large: {len(contents)} bytes (max {_MAX_UPLOAD_BYTES})"
        )

    # See import_zro_csv above: offload to threadpool so this async route
    # doesn't block the event loop with synchronous, lock-holding disk I/O.
    session, import_meta = await run_in_threadpool(
        store.import_generic_bytes, sid, file.filename, contents, fmt
    )
    _maybe_trigger_llm(sid, session)
    return {"session": _session_view(session), "import_summary": import_meta}


@app.get("/api/session/{sid}/report")
def get_report(sid: str):
    session = store.get(sid)
    report = _report_payload(session)
    # Record to per-TV calibration history on first fetch (idempotent via session flag).
    # Only record completed sessions — skip sessions that are still in-flight or have
    # no meaningful measurements (they pollute history with null entries).
    completed_steps = {"luminance", "white_balance", "gamma", "report"}
    has_measurements = (
        session.get("post_measurements") and len(session["post_measurements"]) > 0
    )
    if not session.get("_history_recorded") and session.get("step") in completed_steps and has_measurements:
        try:
            tv_settings = session.get("tv_settings") or {}
            wb_final = {
                "two_point": tv_settings.get("two_point_wb") or {},
                "multipoint": tv_settings.get("multipoint_wb") or {},
            }
            cms_final = tv_settings.get("cms_sliders") or {}
            _record_session(
                tv_key=session.get("tv_key", "unknown"),
                session_id=sid,
                mode=session.get("mode", ""),
                report=report,
                wb_final=wb_final,
                cms_final=cms_final,
            )
            session["_history_recorded"] = True
            store.save_session(sid)
        except Exception:
            logger.warning("Failed to record calibration history for sid=%s", sid, exc_info=True)
            pass  # history recording is non-critical
    return report


@app.get("/api/session/{sid}/report/html")
def get_report_html(sid: str):
    report = _report_payload(store.get(sid))
    return HTMLResponse(_render_report_html(report))


@app.get("/api/session/{sid}/report/pdf")
def get_report_pdf(sid: str):
    report = _report_payload(store.get(sid))
    try:
        pdf_bytes = _render_report_pdf(report)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    tv_slug = "".join(c if c.isalnum() else "_" for c in report.get("tv", "report"))
    filename = f"calibration_{tv_slug}_{sid[:8]}.pdf"
    from fastapi.responses import Response

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Before/After Delta Report endpoints (#168) ────────────────────────────────


def _load_session_by_id(sid: str) -> Dict[str, Any]:
    """Load a session from memory or disk. Raises 404 if not found."""
    try:
        return store.get(sid)
    except HTTPException:
        pass
    path = SESSION_STORE_DIR / f"{sid}.json"
    if not path.exists():
        raise HTTPException(404, f"Session not found: {sid}")
    try:
        from calibrator.session import deserialize_session
        data = json.loads(path.read_text(encoding="utf-8"))
        return deserialize_session(data)
    except Exception as exc:
        raise HTTPException(500, f"Could not load session {sid}: {exc}") from exc


@app.get("/api/report/compare")
def get_report_compare(a: str, b: str, format: str = "json"):
    """Compare two sessions side-by-side.

    Query params:
      a: session ID of the baseline/earlier session
      b: session ID of the more recent session
      format: "json" (default) | "html" | "pdf"
    """
    session_a = _load_session_by_id(a)
    session_b = _load_session_by_id(b)
    comparison = _comparison_payload(session_a, session_b)

    if format == "html":
        return HTMLResponse(_render_comparison_html(comparison))
    if format == "pdf":
        try:
            pdf_bytes = _render_comparison_pdf(comparison)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        from fastapi.responses import Response
        filename = f"comparison_{a[:8]}_vs_{b[:8]}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return comparison


_HISTORY_COMPUTED_METRIC_KEYS = {
    "post_grayscale_avg_de",
    "gamma_avg",
    "wb_avg_de",
    "cms_avg_de",
}


def _compute_metrics_from_session(session_id: str) -> Dict[str, Optional[float]]:
    """Load a session and compute metrics from its report payload.

    Returns None values if the session cannot be loaded or has no target.
    """
    try:
        session = _load_session_by_id(session_id)
        report = _report_payload(session)
        return {
            "post_grayscale_avg_de": (report.get("post_cal") or {}).get("avg_de"),
            "gamma_avg": (report.get("gamma") or {}).get("avg_gamma"),
            "wb_avg_de": (report.get("white_balance") or {}).get("avg_de"),
            "cms_avg_de": (report.get("color_tuner") or {}).get("avg_de"),
            "peak_luminance": report.get("peak_luminance"),
        }
    except Exception:
        logger.warning(
            "Failed to compute metrics for session %s", session_id, exc_info=True
        )
        return {
            "post_grayscale_avg_de": None,
            "gamma_avg": None,
            "wb_avg_de": None,
            "cms_avg_de": None,
            "peak_luminance": None,
        }


def _history_entry_metrics(
    entry: Dict[str, Any], tv_key: str, is_baseline: bool = False
) -> Dict[str, Optional[float]]:
    """Return computed metrics for a history entry.

    Uses stored values if available; otherwise computes from the session's report payload
    and writes back to the history store so recomputation is avoided on subsequent loads.

    Args:
        entry: History or baseline entry dict
        tv_key: TV profile key for write-back
        is_baseline: Whether this is a baseline entry (uses different write-back path)

    Returns:
        Dict of metric values, computed if necessary.
    """
    metrics = {
        "post_grayscale_avg_de": entry.get("post_grayscale_avg_de"),
        "gamma_avg": entry.get("gamma_avg"),
        "wb_avg_de": entry.get("wb_avg_de"),
        "cms_avg_de": entry.get("cms_avg_de"),
        "peak_luminance": entry.get("peak_luminance"),
    }

    # Only trigger recomputation for computed metrics, not peak_luminance
    if any(metrics.get(k) is None for k in _HISTORY_COMPUTED_METRIC_KEYS):
        session_id = entry.get("session_id")
        if not session_id:
            return metrics

        computed = _compute_metrics_from_session(session_id)
        for key, value in computed.items():
            if metrics.get(key) is None:
                metrics[key] = value

        # Write-back computed values to history store (best effort)
        try:
            if is_baseline:
                _update_baseline(tv_key, computed)
            else:
                _update_history_entry(tv_key, session_id, computed)
        except Exception:
            logger.warning(
                "Failed to write-back metrics for session %s", session_id, exc_info=True
            )

    return metrics


@app.get("/api/report/history/{tv_key}")
def get_report_history(tv_key: str, limit: int = 20):
    """Return past calibration sessions for a TV as lightweight summaries.

    Used by the frontend comparison page to let users pick two sessions to compare.
    Full report data is fetched via GET /api/report/compare?a=...&b=...
    Returns the baseline (if any) followed by sessions from most-recent to oldest.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "limit must be between 1 and 100")

    history = _load_history(tv_key, limit=limit)
    baseline = _load_baseline(tv_key)

    sessions = []
    if baseline:
        metrics = _history_entry_metrics(baseline, tv_key, is_baseline=True)
        sessions.append({
            "session_id": baseline.get("session_id"),
            "date": baseline.get("date"),
            "mode": baseline.get("mode"),
            "is_baseline": True,
            "avg_de": metrics["post_grayscale_avg_de"],
            "peak_luminance": metrics["peak_luminance"],
            "gamma_avg": metrics["gamma_avg"],
            "wb_avg_de": metrics["wb_avg_de"],
            "cms_avg_de": metrics["cms_avg_de"],
        })

    for entry in history:
        metrics = _history_entry_metrics(entry, tv_key)
        sessions.append({
            "session_id": entry.get("session_id"),
            "date": entry.get("date"),
            "mode": entry.get("mode"),
            "is_baseline": False,
            "avg_de": metrics["post_grayscale_avg_de"],
            "peak_luminance": metrics["peak_luminance"],
            "gamma_avg": metrics["gamma_avg"],
            "wb_avg_de": metrics["wb_avg_de"],
            "cms_avg_de": metrics["cms_avg_de"],
        })

    return {"tv_key": tv_key, "sessions": sessions}


@app.post("/api/report/compare/delta_summary")
def post_delta_summary(a: str, b: str):
    """Generate an LLM-authored plain-language delta summary for two sessions.

    Returns {"summary": "<prose paragraph>"} or {"summary": null} if no LLM is configured.
    Uses the LLM configuration from session B.
    """
    session_a = _load_session_by_id(a)
    session_b = _load_session_by_id(b)
    comparison = _comparison_payload(session_a, session_b)

    llm_cfg_dict = session_b.get("llm_config") or session_a.get("llm_config") or {}
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        return {"summary": None, "reason": "LLM not configured"}

    llm_cfg = LLMConfig.from_dict(llm_cfg_dict, default_temperature=float(llm_cfg_dict.get("temperature", 0.3)), default_timeout=45.0)
    summary_text = _query_delta_summary(
        comparison["session_a"]["report"],
        comparison["session_b"]["report"],
        comparison["deltas"],
        llm_cfg,
    )
    return {"summary": summary_text, "deltas": comparison["deltas"]}


# ── Suggested patches endpoint (#173) ─────────────────────────────────────────


@app.get("/api/session/{sid}/suggested-patches")
def get_suggested_patches(sid: str, budget: int = 30):
    """Return an LLM-optimized patch list based on current measurement residuals.

    Analyzes per-patch ΔE from all imported measurements and asks the LLM to
    recommend denser sampling where error is highest.  budget caps the total
    patch count (default 30, configurable via query param).

    Returns {"optimization": {...}} with patches list, rationale, confidence,
    and auto_apply flag.  Returns {"optimization": null} if LLM is not configured.
    """
    if budget < 1 or budget > 200:
        raise HTTPException(400, "budget must be between 1 and 200")

    session = store.get(sid)

    all_measurements = _get_all_measurements(session)
    if not all_measurements:
        raise HTTPException(400, "No measurements in session; import data first.")

    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        return {"optimization": None, "reason": "LLM not configured"}

    cfg = _session_to_analysis_config(session)
    patches_core = [_measurement_to_patch(m) for m in all_measurements]
    target = session.get("target")
    white_point = target.white_point_xy if target else None
    summary = _calcore_analyze(patches_core, cfg, white_point)

    llm_cfg = LLMConfig.from_dict(llm_cfg_dict, default_timeout=60.0)
    phase = session.get("step", "baseline")
    optimization = _query_patch_optimization(
        summary.grayscale_rows,
        summary.color_rows,
        phase=phase,
        patch_budget=budget,
        llm=llm_cfg,
        code_max=cfg.code_max,
    )

    if optimization is None:
        return {"optimization": None, "reason": "LLM returned no result"}

    return {"optimization": optimization.to_dict()}


# ── Predicted settings endpoint (#335) ────────────────────────────────────────


@app.get("/api/session/{sid}/predicted-settings")
def get_predicted_settings(sid: str, phase: Optional[str] = None):
    """Return LLM-predicted starting settings for the current calibration step.

    Uses prior-session history (wb_final/cms_final) and TV settings schema to
    produce a warm-start recommendation.  Returns null when LLM is not configured.

    Query params:
        phase: override the current step (default: session.step or "baseline")

    Returns {"predicted": {...} | null, "reason": "<string when null>"}.
    """
    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        return {"predicted": None, "reason": "LLM not configured"}

    tv_key = session.get("tv_key", "unknown")
    history = _load_history(tv_key, limit=3)
    baseline = _load_baseline(tv_key)

    tv_schema: Optional[Dict[str, Any]] = None
    if tv_key:
        profile = _get_tv_profile(tv_key)
        if profile and profile.llm_schema:
            tv_schema = profile.llm_schema

    llm_cfg = LLMConfig.from_dict(llm_cfg_dict, default_timeout=45.0)
    predicted = _predict_initial_settings(
        phase=phase or session.get("step", "baseline"),
        history=history,
        baseline=baseline,
        tv_schema=tv_schema,
        llm=llm_cfg,
    )

    if predicted is None:
        return {"predicted": None, "reason": "LLM returned no result"}

    return {"predicted": predicted.to_dict()}


@app.post("/api/session/{sid}/suggested-patches/run")
def run_suggested_patches(sid: str, body: RunPatchesReq):
    """Forward a patch sequence to the ZRO bridge for measurement.

    Body: {"patches": [{nits, r, g, b, priority, label}, ...]}
    Sends a POST to {_zro_bridge_url}/measure/sequence with the patch list.
    The ZRO bridge must support arbitrary patch sequences (not just fixed grids).
    """
    url = _zro_bridge.get()
    if not url:
        raise HTTPException(
            400,
            'ZRO Bridge URL not configured. Set ZRO_BRIDGE_URL env var or POST /api/zro/bridge/config.',
        )
    if len(body.patches) > 200:
        raise HTTPException(400, "Patch sequence too long (max 200).")

    patches_dict = [p.model_dump() for p in body.patches]
    try:
        resp = httpx.post(
            f"{url}/measure/sequence",
            json={"patches": patches_dict},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            502,
            f"Cannot reach ZRO Bridge at {url} — is start.bat running on the Windows PC?",
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"ZRO Bridge error: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"ZRO Bridge proxy error: {exc}")


# ── Adaptive repass endpoint (#166) ────────────────────────────────────────────

class RepassReq(BaseModel):
    """LLM pass-decision result to drive the repass state transition."""
    action: str
    patches: List[str] = []
    reason: str = ""
    ceiling_reason: Optional[str] = None


def _label_to_rgb(label: str, signal_range: str, code_scale: str = "8bit") -> List[int]:
    """Convert a patch label to RGB code values for ZRO bridge dispatch."""
    import re
    label_lower = label.lower().strip()

    cms_map = CMS_PATCHES
    if signal_range == "full" and code_scale == "10bit":
        cms_map = {
            "red": (1023, 0, 0), "green": (0, 1023, 0), "blue": (0, 0, 1023),
            "cyan": (0, 1023, 1023), "magenta": (1023, 0, 1023), "yellow": (1023, 1023, 0),
        }
    elif signal_range == "full":
        cms_map = {
            "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
            "cyan": (0, 255, 255), "magenta": (255, 0, 255), "yellow": (255, 255, 0),
        }
    for name, rgb in cms_map.items():
        if name.lower() == label_lower:
            return list(rgb)

    if label_lower.startswith("rgb(") and ")" in label_lower:
        inner = label_lower[4:label_lower.index(")")]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 3:
            try:
                return [int(p) for p in parts]
            except ValueError:
                pass

    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%", label_lower)
    if pct_match:
        pct = float(pct_match.group(1))
        cv_val = _cv(pct, signal_range, code_scale)
        return [cv_val, cv_val, cv_val]

    if code_scale == "10bit":
        return [1023, 1023, 1023]
    return [235, 235, 235]


@app.post("/api/session/{sid}/repass")
def post_repass(sid: str, req: RepassReq):
    """Apply an LLM pass-decision to the session state machine."""
    session = store.get(sid)
    signal_range = session.get("signal_range", "full")
    code_scale = session.get("code_scale", "8bit")

    session = store.repass(
        sid=sid, action=req.action, patches=req.patches,
        reason=req.reason, ceiling_reason=req.ceiling_reason,
    )

    dogegen_dispatched = False
    if req.action == "repatch" and req.patches:
        patches_for_bridge = []
        for label in req.patches:
            rgb = _label_to_rgb(label, signal_range, code_scale)
            patches_for_bridge.append({
                "r": rgb[0], "g": rgb[1], "b": rgb[2],
                "label": label, "nits": 100.0,
            })
        bridge_url = _zro_bridge.get()
        if patches_for_bridge and bridge_url:
            try:
                resp = httpx.post(
                    f"{bridge_url}/measure/sequence",
                    json={"patches": patches_for_bridge},
                    timeout=30.0,
                )
                resp.raise_for_status()
                dogegen_dispatched = True
            except Exception as exc:
                logger.warning("Failed to dispatch repatch patches to ZRO Bridge: %s", exc)

    return {
        "session": _session_view(session),
        "decision": {
            "action": req.action, "patches": req.patches,
            "reason": req.reason, "ceiling_reason": req.ceiling_reason,
            "repass_count": session.get("repass_count", 0),
            "dogegen_dispatched": dogegen_dispatched,
        },
    }


# ── Adaptive pass-decision endpoint (#166) ─────────────────────────────────────

class PassDecisionReq(BaseModel):
    measurements: List[Dict[str, Any]]
    repass_count: int = 0


@app.post("/api/session/{sid}/pass-decision")
def post_pass_decision(sid: str, req: PassDecisionReq):
    """Evaluate measurement residuals and decide: accept, repatch, or ceiling.

    Returns {"action": "accept"|"repatch"|"ceiling", "patches": [...], "reason": "...", "confidence": 0.0, "repass_count": N}.
    """
    session = store.get(sid)
    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        return {"action": "accept", "reason": "LLM not configured", "confidence": 1.0, "repass_count": req.repass_count}

    llm_cfg = LLMConfig.from_dict(llm_cfg_dict, default_timeout=45.0, default_temperature=0.0)

    target = session.get("target")
    target_gamma = target.gamma if target else 2.2
    target_peak = target.peak_luminance_nits if target else 120.0
    target_wp = list(target.white_point_xy) if target else [0.3127, 0.3290]
    target_gamut = target.gamut if target else "bt709"

    decision = _query_pass_decision(
        measurements=req.measurements,
        phase=session.get("step", "baseline"),
        signal_range=session.get("signal_range", "full"),
        code_scale=session.get("code_scale", "8bit"),
        target_gamma=target_gamma,
        target_peak_nits=target_peak,
        target_white_point=target_wp,
        target_gamut=target_gamut,
        llm=llm_cfg,
        repass_count=req.repass_count,
    )

    if decision is None:
        return {"action": "accept", "reason": "LLM unavailable", "confidence": 1.0, "repass_count": req.repass_count}

    return {
        "action": decision.action,
        "patches": decision.patches,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "repass_count": decision.repass_count,
        "ceiling_reason": decision.ceiling_reason,
    }


# ── Convergence-aware next-settings endpoint (#337) ────────────────────────────


@app.post("/api/session/{sid}/next-settings")
def post_next_settings(sid: str):
    """Predict the next round of settings, aware of convergence trend and round cap.

    Reads the adjustment rounds recorded on the session, assesses convergence
    against the TV's quality-gate thresholds, and either short-circuits
    (converged → ``verify``; round cap reached or stalled → ``ceiling``) or asks
    the LLM for damped next-round deltas.  When the LLM produces new deltas, this
    round's residual + suggestions are recorded so the next call can measure
    whether they actually converged.

    Returns {"next_settings": {...} | null, "reason": "<string when null>"}.
    """
    session = store.get(sid)

    all_measurements = _get_all_measurements(session)
    if not all_measurements:
        raise HTTPException(400, "No measurements in session; import data first.")

    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        return {"next_settings": None, "reason": "LLM not configured"}

    cfg = _session_to_analysis_config(session)
    patches_core = [_measurement_to_patch(m) for m in all_measurements]
    target = session.get("target")
    white_point = target.white_point_xy if target else None
    summary = _calcore_analyze(patches_core, cfg, white_point)

    phase = session.get("step", "baseline")
    target_gamma = target.gamma if target else None

    # Reuse the per-TV quality-gate thresholds as convergence targets (#166).
    profile = _get_tv_profile(session.get("tv_key", ""))
    thresholds: Optional[Dict[str, Any]] = None
    tv_schema: Optional[Dict[str, Any]] = None
    if profile:
        if getattr(profile, "quality_gate_thresholds", None):
            thresholds = profile.quality_gate_thresholds
        if profile.llm_schema:
            tv_schema = profile.llm_schema

    # Reconstruct current TV slider values when supplied (#96).
    tv_settings: Optional[TVSettings] = None
    raw_tv_settings = session.get("tv_settings")
    if raw_tv_settings:
        tv_settings = TVSettings(
            two_point_wb=raw_tv_settings.get("two_point_wb"),
            multipoint_wb=raw_tv_settings.get("multipoint_wb"),
            cms_sliders=raw_tv_settings.get("cms_sliders"),
        )

    prior_rounds = session.get("llm_adjustment_rounds", [])

    llm_cfg = LLMConfig.from_dict(
        llm_cfg_dict, default_timeout=45.0, default_temperature=0.0
    )
    prediction = _predict_next_settings(
        summary,
        cfg,
        phase,
        llm_cfg,
        prior_rounds=prior_rounds,
        thresholds=thresholds,
        tv_settings=tv_settings,
        tv_schema=tv_schema,
        target_gamma=target_gamma,
    )

    if prediction is None:
        return {"next_settings": None, "reason": "LLM returned no result"}

    # Only a round that produced new deltas to apply advances the loop counter;
    # converged/ceiling short-circuits have no deltas and end the loop.
    if prediction.source == "llm":
        store.record_adjustment_round(
            sid,
            residual={
                "avg_de": prediction.convergence.get("avg_de"),
                "max_de": prediction.convergence.get("max_de"),
                "gamma": prediction.convergence.get("gamma_deviation"),
            },
            suggested=prediction.adjustments,
        )

    return {"next_settings": prediction.to_dict()}


@app.get("/api/adb/status")
def adb_status(device: Optional[str] = None):
    try:
        return _adb.get_adb_status(device=device)
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc


@app.post("/api/adb/cms/push")
def adb_cms_push(device: Optional[str] = None):
    try:
        result = _adb.push_cms_tool(device=device)
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(502, f"Push failed: {result['stderr'] or result['stdout']}")
    return result


@app.post("/api/adb/cms/set")
def adb_cms_set(req: AdbCmsSetReq):
    try:
        result = _adb.set_cms_value(
            channel=req.channel, control=req.control, value=req.value, device=req.device
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(
            502, f"ADB command failed: {result['stderr'] or result['stdout']}"
        )
    return result


@app.post("/api/adb/cms/adjust")
def adb_cms_adjust(req: AdbCmsAdjustReq):
    try:
        result = _adb.adjust_cms_value(
            channel=req.channel, control=req.control, delta=req.delta, device=req.device
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(
            502, f"ADB command failed: {result['stderr'] or result['stdout']}"
        )
    return result


@app.post("/api/adb/cms/get")
def adb_cms_get(req: AdbCmsGetReq):
    try:
        result = _adb.get_cms_value(
            channel=req.channel, control=req.control, device=req.device
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(
            502, f"ADB command failed: {result['stderr'] or result['stdout']}"
        )
    return result


@app.get("/api/adb/cms/all")
def adb_cms_get_all(device: Optional[str] = None):
    try:
        result = _adb.get_all_cms_values(device=device)
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(
            502, f"ADB command failed: {result['stderr'] or result['stdout']}"
        )
    return result


@app.post("/api/adb/cms/reset")
def adb_cms_reset(device: Optional[str] = None):
    try:
        result = _adb.reset_cms(device=device)
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(
            502, f"ADB command failed: {result['stderr'] or result['stdout']}"
        )
    return result


@app.post("/api/adb/picture/set")
def adb_picture_set(req: AdbPictureSetReq):
    try:
        result = _adb.set_picture_control(
            control=req.control, value=req.value, device=req.device
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(
            502, f"ADB command failed: {result['stderr'] or result['stdout']}"
        )
    return result


@app.post("/api/adb/picture/get")
def adb_picture_get(req: AdbPictureGetReq):
    try:
        result = _adb.get_picture_control(control=req.control, device=req.device)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(
            502, f"ADB command failed: {result['stderr'] or result['stdout']}"
        )
    return result


_ZRO_BRIDGE_TIMEOUT = 5.0


@app.get("/api/zro/bridge/status")
@app.get("/api/bridge/status")
def zro_bridge_status(url: Optional[str] = Query(None)):
    target_url = url if url is not None else _zro_bridge.get()
    if not target_url:
        return {
            "configured": False,
            "url": None,
            "ok": False,
            "error": "ZRO Bridge URL not configured",
        }
    try:
        resp = httpx.get(f"{target_url}/status", timeout=_ZRO_BRIDGE_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return {"configured": True, "url": target_url, "ok": True, **data}
    except httpx.ConnectError:
        return {
            "configured": True,
            "url": target_url,
            "ok": False,
            "error": "Cannot reach ZRO Bridge — is start.bat running on the Windows PC?",
        }
    except Exception as exc:
        return {
            "configured": True,
            "url": target_url,
            "ok": False,
            "error": str(exc),
        }


@app.post("/api/zro/bridge/config")
@app.post("/api/bridge/url")
def zro_bridge_config(body: ZroBridgeConfigBody):
    _zro_bridge.set(body.url.rstrip("/"))
    _save_prefs()
    return {"ok": True, "url": _zro_bridge.get()}


@app.get("/api/zro/bridge/instruments")
def zro_bridge_instruments():
    """List meters the bridge's ArgyllCMS backend currently detects (#531)."""
    url = _zro_bridge.get()
    if not url:
        raise HTTPException(400, "ZRO Bridge URL not configured.")
    try:
        resp = httpx.get(f"{url}/instruments", timeout=_ZRO_BRIDGE_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError as exc:
        raise HTTPException(
            502, "Cannot reach ZRO Bridge — is start.bat running on the Windows PC?"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc


@app.post("/api/zro/bridge/instrument")
def zro_bridge_select_instrument(body: ZroBridgeInstrumentBody):
    """
    Persist the selected ArgyllCMS meter and CCMX/CCSS correction path
    across app sessions (#531, #535) and push them to the bridge so they
    take effect immediately. If the bridge is unreachable the selection
    still saves — it applies next time the app reconnects and re-pushes it.
    """
    _prefs["argyll"] = {
        "port": body.port or "",
        "instrument_name": body.instrument_name or "",
        "correction_path": body.correction_path or "",
    }
    _save_prefs()

    pushed = False
    url = _zro_bridge.get()
    if url:
        try:
            resp = httpx.post(
                f"{url}/config/argyll-port",
                json={"port": body.port, "correction_path": body.correction_path},
                timeout=_ZRO_BRIDGE_TIMEOUT,
            )
            resp.raise_for_status()
            pushed = True
        except Exception:
            logger.warning("Could not push argyll port selection to bridge at %s", url, exc_info=True)

    return {"ok": True, "argyll": _prefs["argyll"], "pushed_to_bridge": pushed}


@app.get("/api/prefs")
def get_prefs():
    return _prefs


@app.post("/api/prefs")
def save_prefs_endpoint(req: PrefsReq):
    sd = _prefs["session_defaults"]
    if req.signal_range is not None:
        if req.signal_range not in ("full", "limited"):
            raise HTTPException(400, "signal_range must be 'full' or 'limited'")
        sd["signal_range"] = req.signal_range
    if req.code_scale is not None:
        if req.code_scale not in ("8bit", "10bit"):
            raise HTTPException(400, "code_scale must be '8bit' or '10bit'")
        sd["code_scale"] = req.code_scale
    if req.pattern_generator is not None:
        sd["pattern_generator"] = req.pattern_generator
    if req.llm_endpoint is not None:
        _prefs["llm"]["endpoint"] = req.llm_endpoint.strip()
    if req.llm_model is not None:
        _prefs["llm"]["model"] = req.llm_model.strip()
    if req.watch_folder is not None:
        _prefs["watch_folder"] = req.watch_folder.strip()
    autocal_prefs = _prefs.setdefault("autocal", dict(_AUTOCAL_DEFAULTS))
    if req.autocal_apply_mode is not None:
        if req.autocal_apply_mode not in ("manual", "adb"):
            raise HTTPException(400, "autocal_apply_mode must be 'manual' or 'adb'")
        autocal_prefs["apply_mode"] = req.autocal_apply_mode
    if req.autocal_damping is not None:
        if not (0.0 < req.autocal_damping <= 1.0):
            raise HTTPException(400, "autocal_damping must be in (0, 1]")
        autocal_prefs["damping"] = req.autocal_damping
    if req.autocal_max_iterations is not None:
        if not (1 <= req.autocal_max_iterations <= 50):
            raise HTTPException(400, "autocal_max_iterations must be between 1 and 50")
        autocal_prefs["max_iterations"] = req.autocal_max_iterations
    if req.autocal_skip_stalled_controls is not None:
        autocal_prefs["skip_stalled_controls"] = req.autocal_skip_stalled_controls
    if req.autocal_bridge_timeout is not None:
        if not (1.0 <= req.autocal_bridge_timeout <= 120.0):
            raise HTTPException(400, "autocal_bridge_timeout must be between 1 and 120 seconds")
        autocal_prefs["bridge_timeout"] = req.autocal_bridge_timeout
    if req.autocal_bridge_poll_interval is not None:
        if not (0.05 <= req.autocal_bridge_poll_interval <= 5.0):
            raise HTTPException(400, "autocal_bridge_poll_interval must be between 0.05 and 5 seconds")
        autocal_prefs["bridge_poll_interval"] = req.autocal_bridge_poll_interval
    _save_prefs()
    return {"ok": True, **_prefs}


@app.post("/api/zro/trigger")
@app.post("/api/bridge/measure")
def zro_trigger(body: ZroBridgeMeasureBody = Body(default_factory=dict)):
    if isinstance(body, dict):
        target_url = body.get("url") if "url" in body else _zro_bridge.get()
    else:
        target_url = body.url if body.url is not None else _zro_bridge.get()
    if not target_url:
        raise HTTPException(
            400,
            'ZRO Bridge URL not configured.  Set ZRO_BRIDGE_URL env var or POST /api/zro/bridge/config { "url": "http://<windows-pc>:7070" }',
        )
    try:
        resp = httpx.post(f"{target_url}/measure", timeout=_ZRO_BRIDGE_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            502,
            f"Cannot reach ZRO Bridge at {target_url} — is start.bat running on the Windows PC?",
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"ZRO Bridge error: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"ZRO Bridge proxy error: {exc}")


# ── Autocal: guided closed-loop measure → correct → apply → re-measure ───────


def _iteration_event_to_dict(event: Any) -> Dict[str, Any]:
    return {
        "colour": event.colour,
        "control": event.control,
        "iteration": event.iteration,
        "error": event.error,
        "delta_e": event.delta_e,
        "step": event.correction.step,
        "new_value": event.correction.new_value,
        "damping": event.correction.damping,
        "gain": event.correction.gain,
        "gain_source": event.correction.gain_source,
        "oscillating": event.correction.oscillating,
        "stalled": event.correction.stalled,
        "out_of_range": event.correction.out_of_range,
        "reason": event.correction.reason,
        "apply_ok": event.apply_result.ok,
        "apply_message": event.apply_result.message,
        "requires_user_action": event.apply_result.requires_user_action,
    }


def _autocal_get_confirm_event(sid: str) -> threading.Event:
    with _autocal_confirm_lock:
        ev = _autocal_confirm_events.get(sid)
        if ev is None:
            ev = threading.Event()
            _autocal_confirm_events[sid] = ev
        return ev


def _run_autocal_background(
    sid: str,
    loop: AutocalLoop,
    colours: List[str],
    patch_for_colour: Any,
) -> None:
    def on_iteration(event: Any) -> None:
        _autocal_broadcast(sid, {"event": "autocal_iteration", "data": _iteration_event_to_dict(event)})

    def on_waiting(colour: str, iteration: int) -> None:
        _autocal_broadcast(sid, {"event": "autocal_waiting", "data": {"colour": colour, "iteration": iteration}})

    confirm_event = _autocal_get_confirm_event(sid)
    confirm_event.clear()

    def confirm_step() -> bool:
        # Poll rather than wait indefinitely so a stop request (which only
        # sets loop.cancel(), not this event) is noticed within ~1s.
        while not loop.cancelled:
            if confirm_event.wait(timeout=1.0):
                confirm_event.clear()
                return True
        return False

    loop.on_iteration = on_iteration
    loop.on_waiting = on_waiting
    loop.confirm_step = confirm_step
    _autocal_broadcast(sid, {"event": "autocal_start", "data": {"colours": colours}})
    try:
        result = loop.run(colours, patch_for_colour)
        history_payload: Dict[str, Any] = {"colours": {}, "cancelled": result.cancelled}
        for colour, cr in result.colours.items():
            history_payload["colours"][colour] = {
                "converged": cr.converged,
                "reason": cr.stopped_reason,
                "delta_e": cr.delta_e,
                "iterations": cr.iterations,
                "history": [_iteration_event_to_dict(e) for e in cr.history],
            }
            _autocal_broadcast(
                sid,
                {
                    "event": "autocal_colour_done",
                    "data": {
                        "colour": colour,
                        "converged": cr.converged,
                        "reason": cr.stopped_reason,
                        "delta_e": cr.delta_e,
                        "iterations": cr.iterations,
                    },
                },
            )
        with _autocal_history_lock:
            _autocal_last_run[sid] = history_payload
        _autocal_broadcast(sid, {"event": "autocal_done", "data": {"cancelled": result.cancelled}})
    except Exception as exc:
        logger.error("Autocal run failed  sid=%s error=%s", sid, exc, exc_info=True)
        _autocal_broadcast(sid, {"event": "autocal_error", "data": str(exc)})
    finally:
        with _autocal_loops_lock:
            _autocal_loops.pop(sid, None)
        with _autocal_confirm_lock:
            _autocal_confirm_events.pop(sid, None)


@app.post("/api/session/{sid}/autocal/run")
def autocal_run(sid: str, req: AutocalRunReq):
    session = store.get(sid)
    target = session.get("target")
    if not target:
        raise HTTPException(400, "Session has no calibration target set.")
    tv_key = session.get("tv_key", "")
    tv = _get_tv_profile(tv_key) if tv_key else None
    if not tv:
        raise HTTPException(400, "No TV profile selected for this session.")

    with _autocal_loops_lock:
        if sid in _autocal_loops:
            raise HTTPException(409, "Autocal is already running for this session.")

    colours = req.colours or list(tv.CMS_COLOURS)
    unknown = [c for c in colours if c not in tv.CMS_COLOURS]
    if unknown:
        raise HTTPException(400, f"Unknown CMS colours for {tv_key}: {unknown}")

    autocal_prefs = _prefs.get("autocal", _AUTOCAL_DEFAULTS)
    apply_mode = req.apply_mode or autocal_prefs.get("apply_mode", "manual")
    if apply_mode not in ("manual", "adb"):
        raise HTTPException(400, "apply_mode must be 'manual' or 'adb'.")
    damping = req.damping if req.damping is not None else autocal_prefs.get("damping", ControllerConfig().damping)
    if not (0.0 < damping <= 1.0):
        raise HTTPException(400, "damping must be in (0, 1].")
    max_iterations = (
        req.max_iterations
        if req.max_iterations is not None
        else autocal_prefs.get("max_iterations", 8)
    )
    if not (1 <= max_iterations <= 50):
        raise HTTPException(400, "max_iterations must be between 1 and 50.")
    skip_stalled_controls = (
        req.skip_stalled_controls
        if req.skip_stalled_controls is not None
        else autocal_prefs.get("skip_stalled_controls", False)
    )
    bridge_timeout = (
        req.bridge_timeout
        if req.bridge_timeout is not None
        else autocal_prefs.get("bridge_timeout", _AUTOCAL_BRIDGE_TIMEOUT)
    )
    if not (1.0 <= bridge_timeout <= 120.0):
        raise HTTPException(400, "bridge_timeout must be between 1 and 120 seconds.")
    bridge_poll_interval = (
        req.bridge_poll_interval
        if req.bridge_poll_interval is not None
        else autocal_prefs.get("bridge_poll_interval", _AUTOCAL_POLL_INTERVAL)
    )
    if not (0.05 <= bridge_poll_interval <= 5.0):
        raise HTTPException(400, "bridge_poll_interval must be between 0.05 and 5 seconds.")

    apply_target: ApplyTarget = (
        ManualApplyTarget()
        if apply_mode == "manual"
        # ADB auto-apply falls back to a manual instruction (and the loop
        # pauses for user confirmation) on any ADB failure — roadmap Item 1f.
        else FallbackApplyTarget(AdbApplyTarget(device=req.device), ManualApplyTarget())
    )
    config = ControllerConfig(damping=damping)
    measurement_source = _SessionCmsMeasurementSource(
        sid, timeout=bridge_timeout, poll_interval=bridge_poll_interval
    )
    loop = AutocalLoop(
        measurement_source,
        apply_target,
        target,
        list(tv.CMS_CONTROLS),
        signal_range=session.get("signal_range", "auto"),
        code_scale=session.get("code_scale", "8bit"),
        config=config,
        max_iterations=max_iterations,
        skip_stalled_controls=skip_stalled_controls,
    )
    with _autocal_loops_lock:
        _autocal_loops[sid] = loop
    with _autocal_history_lock:
        _autocal_last_run.pop(sid, None)

    signal_range = session.get("signal_range", "auto")
    code_scale = session.get("code_scale", "8bit")
    patch_rgb = _cms_patches(signal_range, code_scale)

    def patch_for_colour(colour: str) -> Patch:
        rgb = patch_rgb.get(colour, (235, 235, 235))
        return Patch(
            label=colour, r_target=rgb[0], g_target=rgb[1], b_target=rgb[2], meas_xyz=(0, 0, 0), kind="color"
        )

    t = threading.Thread(
        target=_run_autocal_background,
        args=(sid, loop, colours, patch_for_colour),
        daemon=True,
    )
    t.start()
    return {
        "status": "running",
        "colours": colours,
        "apply_mode": apply_mode,
        "damping": damping,
        "max_iterations": max_iterations,
        "skip_stalled_controls": skip_stalled_controls,
        "bridge_timeout": bridge_timeout,
        "bridge_poll_interval": bridge_poll_interval,
    }


@app.post("/api/session/{sid}/autocal/stop")
def autocal_stop(sid: str):
    store.get(sid)  # raises 404 if session doesn't exist
    with _autocal_loops_lock:
        loop = _autocal_loops.get(sid)
    if not loop:
        return {"status": "not_running"}
    loop.cancel()
    return {"status": "stopping"}


@app.post("/api/session/{sid}/autocal/confirm")
def autocal_confirm(sid: str):
    """Unblocks a running autocal loop that's paused waiting for the user to
    apply a manual (or ADB-fallback) instruction on the TV — call this after
    making the change, in lieu of a physical "Remeasure" button."""
    store.get(sid)  # raises 404 if session doesn't exist
    with _autocal_loops_lock:
        loop = _autocal_loops.get(sid)
    if not loop:
        return {"status": "not_running"}
    with _autocal_confirm_lock:
        ev = _autocal_confirm_events.get(sid)
    if ev is None:
        return {"status": "not_waiting"}
    ev.set()
    return {"status": "confirmed"}


@app.get("/api/session/{sid}/autocal/history")
def autocal_history(sid: str):
    store.get(sid)  # raises 404 if session doesn't exist
    with _autocal_history_lock:
        recorded = _autocal_last_run.get(sid)
    if recorded is None:
        return {"available": False}
    return {"available": True, **recorded}


@app.get("/api/session/{sid}/autocal/stream")
def autocal_stream(sid: str):
    store.get(sid)  # raises 404 if session doesn't exist
    ev_queue = _autocal_subscribe(sid)

    def _generator():
        try:
            while True:
                try:
                    payload = ev_queue.get(timeout=20.0)
                    event_type = payload.get("event", "autocal_iteration")
                    data = json.dumps(payload.get("data", {}))
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            _autocal_unsubscribe(sid, ev_queue)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/session/{sid}/watch")
def session_watch_start(sid: str, body: WatchStartBody):
    return watch_config(WatchConfigBody(sid=sid, path=body.path))


@app.post("/api/watch/stop")
def watch_stop():
    return watch_config_delete()


_WATCH_ROOT = Path(os.getenv("WATCH_ROOT", Path.home().resolve())).resolve()


@app.post("/api/watch/config")
def watch_config(body: WatchConfigBody):
    sid = body.sid
    session = store.get(sid)
    abs_path = Path(body.path).resolve()
    try:
        abs_path.relative_to(_WATCH_ROOT)
    except ValueError:
        raise HTTPException(400, "Watch path must be within allowed directory")
    csv_parent_exists = str(abs_path).lower().endswith(".csv") and os.path.isdir(
        os.path.dirname(str(abs_path)) or "."
    )
    if not (os.path.isdir(str(abs_path)) or csv_parent_exists):
        raise HTTPException(400, f"Watch path does not exist: {body.path!r}")
    try:
        _fw_start(
            body.path,
            lambda: _sessions.get(sid),
            lambda: _save_session(sid),
            measurement_deserializer=_deserialize_measurement,
            grayscale_level_count=len(
                _grayscale_levels_for_ramp(session.get("grayscale_ramp_steps", 11))
            ),
            post_import_hook=lambda sess: _maybe_trigger_llm(sid, sess),
            session_lock=store._lock,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _watched_session.set(sid)
    return _watch_status_payload()


@app.delete("/api/watch/config")
def watch_config_delete():
    _fw_stop()
    _watched_session.set(None)
    return _watch_status_payload()


@app.get("/api/watch/status")
def watch_status():
    return _watch_status_payload()


@app.get("/api/watch/events")
def watch_events():
    ev_queue = _fw_subscribe()

    def _generator():
        try:
            while True:
                try:
                    payload = ev_queue.get(timeout=20.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            _fw_unsubscribe(ev_queue)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/events/{sid}")
def session_events(sid: str):
    store.get(sid)
    ev_queue = _fw_subscribe()

    def _generator():
        try:
            session = _sessions.get(sid)
            if session:
                yield f"event: session\ndata: {json.dumps(_session_view(session))}\n\n"
            yield f"event: watch_status\ndata: {json.dumps(_watch_status_payload())}\n\n"
            while True:
                try:
                    ev_queue.get(timeout=20.0)
                    session = _sessions.get(sid)
                    if session:
                        yield f"event: session\ndata: {json.dumps(_session_view(session))}\n\n"
                    yield f"event: watch_status\ndata: {json.dumps(_watch_status_payload())}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            _fw_unsubscribe(ev_queue)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/logs/stream")
def logs_stream():
    q = _log_buffer.subscribe()

    def _generator():
        try:
            # Send the recent buffer first so the panel populates immediately
            for line in _log_buffer.snapshot():
                yield f"data: {json.dumps(line)}\n\n"
            while True:
                try:
                    line = q.get(timeout=20.0)
                    yield f"data: {json.dumps(line)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            _log_buffer.unsubscribe(q)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


_static = Path(__file__).parent / "static"
_static.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static)), name="static")

_assets = _static / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")


@app.get("/")
def root():
    return FileResponse(str(_static / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
