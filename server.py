#!/usr/bin/env python3
"""
ZRO Calibration Helper â€” web API backend.

Run with:
    uvicorn server:app --host 0.0.0.0 --port 8000
    # or: python server.py
"""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10MB limit for CSV uploads
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from calcore.analysis import analyze as _calcore_analyze
from calcore.gamut import (
    assess_gamut_constraints as _assess_gamut_constraints,
    format_gamut_diagnosis as _format_gamut_diagnosis,
    gamut_diagnosis_to_dict as _gamut_diagnosis_to_dict,
)
from calcore.llm import (
    build_history_block as _build_history_block,
    call_llm as _call_llm,
    parse_adjustment_plan as _parse_adjustment_plan,
    probe_llm as _probe_llm,
    query_delta_summary as _query_delta_summary,
    query_gamut_advice as _query_gamut_advice,
    query_next_patch_strategy as _query_next_patch_strategy,
    query_patch_optimization as _query_patch_optimization,
    query_remediation as _query_remediation,
)
from calcore.models import AnalysisConfig, LLMConfig, Patch, TVSettings
from calcore.phase import determine_phase as _determine_phase
from calibrator import TV_PROFILES, get_tv_profile as _get_tv_profile
from calibrator.history import (
    history_summary as _history_summary,
    load_baseline as _load_baseline,
    load_history as _load_history,
    record_session as _record_session,
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
from calibrator.session import (
    CMS_PATCHES,
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
import calibrator.adb_control as _adb

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
    yield


app = FastAPI(title="ZRO Calibration Helper", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SESSION_STORE_DIR = Path(__file__).parent / ".sessions"
SESSION_TTL = timedelta(days=7)
_watched_session_id: Optional[str] = None

_dogegen_proc: Optional[subprocess.Popen] = None
_dogegen_launch_cmd: List[str] = []
_dogegen_last_error: Optional[str] = None
_dogegen_started_at: Optional[datetime] = None
_dogegen_lock = threading.RLock()  # Guards all reads/writes of _dogegen_proc
_DOGEGEN_READY_DELAY_SECONDS = 2.0
_dogegen_config: Dict[str, Any] = {
    "path": os.getenv("DOGEGEN_PATH", "").strip(),
    "resolve_host": os.getenv("DOGEGEN_RESOLVE_HOST", "").strip(),
    "window_pct": int(os.getenv("DOGEGEN_WINDOW_PCT", "10")),
    "maxcll": int(os.getenv("DOGEGEN_MAXCLL", "1000")),
}

# Per-session LLM SSE subscriber queues
_llm_queues: Dict[str, List[queue.Queue]] = {}
_llm_queues_lock = threading.Lock()

store = SessionStore(
    session_dir_getter=lambda: SESSION_STORE_DIR,
    ttl_getter=lambda: SESSION_TTL,
    watched_session_id_getter=lambda: _watched_session_id,
)
store.load_sessions()

_sessions = store.sessions

# ---------------------------------------------------------------------------
# Preferences — persisted to .prefs.json, loaded on startup
# ---------------------------------------------------------------------------
_PREFS_PATH = Path(__file__).parent / ".prefs.json"
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
}


def _load_prefs() -> None:
    """Read .prefs.json and apply to live globals. Env vars set initial values;
    saved prefs overwrite them so the user's last UI choice always wins."""
    global _zro_bridge_url
    if not _PREFS_PATH.exists():
        return
    try:
        saved = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    for key in ("dogegen", "bridge_url", "watch_folder", "llm", "session_defaults"):
        if key in saved:
            _prefs[key] = saved[key]
    for field in ("path", "resolve_host", "window_pct", "maxcll"):
        if field in _prefs.get("dogegen", {}):
            _dogegen_config[field] = _prefs["dogegen"][field]
    if _prefs.get("bridge_url"):
        _zro_bridge_url = _prefs["bridge_url"]


def _save_prefs() -> None:
    """Snapshot current globals into _prefs and write atomically."""
    _prefs["dogegen"] = dict(_dogegen_config)
    _prefs["bridge_url"] = _zro_bridge_url
    try:
        tmp = _PREFS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_prefs, indent=2), encoding="utf-8")
        tmp.replace(_PREFS_PATH)
    except Exception:
        pass  # best-effort — never crash on a prefs write failure


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


class _AdbCmsSetReq(BaseModel):
    channel: str
    control: str
    value: int
    device: Optional[str] = None


class _AdbCmsGetReq(BaseModel):
    channel: str
    control: str
    device: Optional[str] = None


class _AdbPictureSetReq(BaseModel):
    control: str
    value: int
    device: Optional[str] = None


class _AdbPictureGetReq(BaseModel):
    control: str
    device: Optional[str] = None


class TvSettingsReq(BaseModel):
    """Current TV hardware slider values for LLM context (#96)."""

    two_point_wb: Optional[Dict[str, int]] = None
    multipoint_wb: Optional[Dict[str, Any]] = None
    cms_sliders: Optional[Dict[str, Any]] = None


class _ZroBridgeConfigBody(BaseModel):
    url: str


class _WatchConfigBody(BaseModel):
    path: str
    sid: str


class _WatchStartBody(BaseModel):
    path: str


class LlmConfigureReq(BaseModel):
    endpoint: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    temperature: Optional[float] = None
    timeout: Optional[float] = None


class SuggestedPatchBody(BaseModel):
    nits: float
    r: int
    g: int
    b: int
    priority: str
    label: str = ""
    rationale: str = ""


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
    global _dogegen_proc, _dogegen_started_at
    with _dogegen_lock:
        if _dogegen_proc is None:
            return False
        if _dogegen_proc.poll() is None:
            return True
        _dogegen_proc = None
        _dogegen_started_at = None
        return False


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
        elapsed = (
            0.0
            if _dogegen_started_at is None
            else (_now() - _dogegen_started_at).total_seconds()
        )
        ready = elapsed >= _DOGEGEN_READY_DELAY_SECONDS
        if not ready:
            ready_in_ms = max(0, int((_DOGEGEN_READY_DELAY_SECONDS - elapsed) * 1000))
    return {
        "configured": bool(path),
        "path": path,
        "running": running,
        "pid": _dogegen_proc.pid if managed_running and _dogegen_proc else external_pid,
        "managed": managed_running,
        "ready": ready,
        "ready_in_ms": ready_in_ms,
        "resolve_host": _dogegen_config.get("resolve_host") or "",
        "window_pct": int(_dogegen_config.get("window_pct") or 10),
        "maxcll": int(_dogegen_config.get("maxcll") or 1000),
        "last_error": _dogegen_last_error,
        "launch_cmd": list(_dogegen_launch_cmd),
    }


def _dogegen_command_for_session(session: Dict[str, Any], exe_path: str) -> List[str]:
    mode = session.get("mode")
    window_pct = int(_dogegen_config.get("window_pct") or 10)
    maxcll = int(_dogegen_config.get("maxcll") or 1000)
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
    global _dogegen_proc, _dogegen_launch_cmd, _dogegen_last_error, _dogegen_started_at
    with _dogegen_lock:
        if _managed_dogegen_is_running() or _external_dogegen_pid() is not None:
            return {"ok": True, "already_running": True, **_dogegen_status_payload()}
        exe_path = _find_dogegen_executable()
        if not exe_path:
            _dogegen_last_error = (
                "Dogegen.exe not found. Set DOGEGEN_PATH, configure it in the app, "
                "or place it at tools/dogegen/Dogegen.exe."
            )
            raise HTTPException(400, _dogegen_last_error)
        cmd = _dogegen_command_for_session(session, exe_path)
        try:
            kwargs: Dict[str, Any] = {"cwd": str(Path(exe_path).parent)}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            _dogegen_proc = subprocess.Popen(cmd, **kwargs)
            _dogegen_started_at = _now()
            _dogegen_launch_cmd = list(cmd)
            _dogegen_last_error = None
            return {"ok": True, "already_running": False, **_dogegen_status_payload()}
        except Exception as exc:
            _dogegen_proc = None
            _dogegen_started_at = None
            _dogegen_launch_cmd = list(cmd)
            _dogegen_last_error = str(exc)
            raise HTTPException(500, f"Failed to start Dogegen: {exc}") from exc


def _stop_dogegen() -> Dict[str, Any]:
    global _dogegen_proc, _dogegen_started_at
    with _dogegen_lock:
        if not _managed_dogegen_is_running():
            return {"ok": True, "already_stopped": True, **_dogegen_status_payload()}
        try:
            assert _dogegen_proc is not None
            _dogegen_proc.terminate()
            _dogegen_proc.wait(timeout=3)
        except Exception:
            try:
                assert _dogegen_proc is not None
                _dogegen_proc.kill()
            except Exception:
                pass
        finally:
            _dogegen_proc = None
            _dogegen_started_at = None
        return {"ok": True, "already_stopped": False, **_dogegen_status_payload()}


def _watch_status_payload() -> Dict[str, Any]:
    status = _fw_status()
    status["session_id"] = _watched_session_id
    status["session_exists"] = bool(
        _watched_session_id and _watched_session_id in _sessions
    )
    return status


def _llm_subscribe(sid: str) -> "queue.Queue[Dict[str, Any]]":
    q: queue.Queue = queue.Queue()
    with _llm_queues_lock:
        _llm_queues.setdefault(sid, []).append(q)
    return q


def _llm_unsubscribe(sid: str, q: "queue.Queue[Dict[str, Any]]") -> None:
    with _llm_queues_lock:
        listeners = _llm_queues.get(sid, [])
        if q in listeners:
            listeners.remove(q)
        if not listeners:
            _llm_queues.pop(sid, None)


def _llm_broadcast(sid: str, payload: Dict[str, Any]) -> None:
    with _llm_queues_lock:
        listeners = list(_llm_queues.get(sid, []))
    for q in listeners:
        q.put(payload)


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

    return AnalysisConfig(
        mode=calcore_mode,
        eotf=eotf,
        target_space=target_space,
        code_max=code_max,
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
    logger.info(
        "LLM run started  sid=%s phase=%s endpoint=%s model=%s",
        sid,
        phase,
        llm_cfg.endpoint,
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
        endpoint_repr = endpoint.split("?")[0] if endpoint else ""
        logger.info(
            "LLM skip  sid=%s reason=not_configured endpoint=%r model=%r",
            sid,
            endpoint_repr,
            model,
        )
        return

    all_measurements = (
        session.get("pre_measurements", [])
        + session.get("wb_measurements", [])
        + session.get("gamma_measurements", [])
        + session.get("cms_measurements", [])
        + session.get("post_measurements", [])
    )
    if not all_measurements:
        logger.info("LLM skip  sid=%s reason=no_measurements", sid)
        return

    patches = [_measurement_to_patch(m) for m in all_measurements]
    cfg = _session_to_analysis_config(session)
    llm_cfg = LLMConfig(
        endpoint=llm_cfg_dict.get("endpoint", ""),
        model=llm_cfg_dict.get("model", ""),
        api_key=llm_cfg_dict.get("api_key", ""),
        temperature=float(llm_cfg_dict.get("temperature", 0.2)),
        timeout=float(llm_cfg_dict.get("timeout", 30.0)),
    )
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
            pass
    if sd.get("code_scale"):
        try:
            session = store.set_code_scale(sid, sd["code_scale"])
        except Exception:
            pass
    if sd.get("pattern_generator"):
        try:
            session = store.set_pattern_generator(sid, sd["pattern_generator"])
        except Exception:
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


@app.post("/api/session/{sid}/llm/run")
def llm_run(sid: str):
    session = store.get(sid)
    llm_cfg_dict = session.get("llm_config", {})

    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        raise HTTPException(
            400, "LLM not configured; POST /api/session/{sid}/llm/configure first."
        )

    all_measurements = (
        session.get("pre_measurements", [])
        + session.get("wb_measurements", [])
        + session.get("gamma_measurements", [])
        + session.get("cms_measurements", [])
        + session.get("post_measurements", [])
    )
    if not all_measurements:
        raise HTTPException(
            400, "No measurements in session; import data before running LLM analysis."
        )

    patches = [_measurement_to_patch(m) for m in all_measurements]
    cfg = _session_to_analysis_config(session)
    llm_cfg = LLMConfig(
        endpoint=llm_cfg_dict.get("endpoint", ""),
        model=llm_cfg_dict.get("model", ""),
        api_key=llm_cfg_dict.get("api_key", ""),
        temperature=float(llm_cfg_dict.get("temperature", 0.2)),
        timeout=float(llm_cfg_dict.get("timeout", 30.0)),
    )
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
    summary = _calcore_analyze(patches, cfg)
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
    summary = _calcore_analyze(patches, cfg)
    diagnosis = _assess_gamut_constraints(summary.color_rows, cfg.target_space)
    diagnosis_text = _format_gamut_diagnosis(diagnosis)

    llm_cfg = LLMConfig(
        endpoint=llm_cfg_dict.get("endpoint", ""),
        model=llm_cfg_dict.get("model", ""),
        api_key=llm_cfg_dict.get("api_key", ""),
        temperature=float(llm_cfg_dict.get("temperature", 0.2)),
        timeout=float(llm_cfg_dict.get("timeout", 30.0)),
    )
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


class _HardwareEventReq(BaseModel):
    event_type: str
    context: dict = {}
    attempt_count: int = 1
    session_phase: str = ""


@app.post("/api/session/{sid}/hardware/remediate")
def hardware_remediate(sid: str, req: _HardwareEventReq):
    """Ask the LLM to diagnose a hardware fault and suggest recovery steps."""
    session = store.get(sid)
    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        raise HTTPException(400, "LLM not configured.")
    llm_cfg = LLMConfig(
        endpoint=llm_cfg_dict.get("endpoint", ""),
        model=llm_cfg_dict.get("model", ""),
        api_key=llm_cfg_dict.get("api_key", ""),
        temperature=0.0,
        timeout=20.0,
    )
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

    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            413, f"File too large: {len(contents)} bytes (max {MAX_UPLOAD_SIZE_BYTES})"
        )

    session, import_meta = store.import_zro_bytes(sid, file.filename, contents)
    _maybe_trigger_llm(sid, session)
    return {"session": _session_view(session), "import_summary": import_meta}


@app.post("/api/session/{sid}/import/generic")
async def import_generic_csv(sid: str, file: UploadFile = File(...)):
    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(400, f"Could not read uploaded file: {exc}") from exc

    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            413, f"File too large: {len(contents)} bytes (max {MAX_UPLOAD_SIZE_BYTES})"
        )

    session, import_meta = store.import_generic_bytes(sid, file.filename, contents)
    _maybe_trigger_llm(sid, session)
    return {"session": _session_view(session), "import_summary": import_meta}


@app.get("/api/session/{sid}/report")
def get_report(sid: str):
    session = store.get(sid)
    report = _report_payload(session)
    # Record to per-TV calibration history on first fetch (idempotent via session flag).
    if not session.get("_history_recorded"):
        try:
            _record_session(
                tv_key=session.get("tv_key", "unknown"),
                session_id=sid,
                mode=session.get("mode", ""),
                report=report,
            )
            session["_history_recorded"] = True
            store.save_session(sid)
        except Exception:
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
        sessions.append({
            "session_id": baseline.get("session_id"),
            "date": baseline.get("date"),
            "mode": baseline.get("mode"),
            "is_baseline": True,
            "avg_de": baseline.get("post_grayscale_avg_de"),
            "peak_luminance": baseline.get("peak_luminance"),
            "gamma_avg": baseline.get("gamma_avg"),
            "wb_avg_de": baseline.get("wb_avg_de"),
            "cms_avg_de": baseline.get("cms_avg_de"),
        })

    for entry in history:
        sessions.append({
            "session_id": entry.get("session_id"),
            "date": entry.get("date"),
            "mode": entry.get("mode"),
            "is_baseline": False,
            "avg_de": entry.get("post_grayscale_avg_de"),
            "peak_luminance": entry.get("peak_luminance"),
            "gamma_avg": entry.get("gamma_avg"),
            "wb_avg_de": entry.get("wb_avg_de"),
            "cms_avg_de": entry.get("cms_avg_de"),
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

    llm_cfg = LLMConfig(
        endpoint=llm_cfg_dict.get("endpoint", ""),
        model=llm_cfg_dict.get("model", ""),
        api_key=llm_cfg_dict.get("api_key", ""),
        temperature=float(llm_cfg_dict.get("temperature", 0.3)),
        timeout=float(llm_cfg_dict.get("timeout", 45.0)),
    )
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

    all_measurements = (
        session.get("pre_measurements", [])
        + session.get("wb_measurements", [])
        + session.get("gamma_measurements", [])
        + session.get("cms_measurements", [])
        + session.get("post_measurements", [])
    )
    if not all_measurements:
        raise HTTPException(400, "No measurements in session; import data first.")

    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        return {"optimization": None, "reason": "LLM not configured"}

    cfg = _session_to_analysis_config(session)
    patches_core = [_measurement_to_patch(m) for m in all_measurements]
    summary = _calcore_analyze(patches_core, cfg)

    llm_cfg = LLMConfig(
        endpoint=llm_cfg_dict.get("endpoint", ""),
        model=llm_cfg_dict.get("model", ""),
        api_key=llm_cfg_dict.get("api_key", ""),
        temperature=float(llm_cfg_dict.get("temperature", 0.2)),
        timeout=float(llm_cfg_dict.get("timeout", 60.0)),
    )
    phase = session.get("step", "baseline")
    optimization = _query_patch_optimization(
        summary.grayscale_rows,
        summary.color_rows,
        phase=phase,
        patch_budget=budget,
        llm=llm_cfg,
    )

    if optimization is None:
        return {"optimization": None, "reason": "LLM returned no result"}

    return {"optimization": optimization.to_dict()}


@app.post("/api/session/{sid}/suggested-patches/run")
def run_suggested_patches(sid: str, body: RunPatchesReq):
    """Forward a patch sequence to the ZRO bridge for measurement.

    Body: {"patches": [{nits, r, g, b, priority, label}, ...]}
    Sends a POST to {_zro_bridge_url}/measure/sequence with the patch list.
    The ZRO bridge must support arbitrary patch sequences (not just fixed grids).
    """
    global _zro_bridge_url
    if not _zro_bridge_url:
        raise HTTPException(
            400,
            'ZRO Bridge URL not configured. Set ZRO_BRIDGE_URL env var or POST /api/zro/bridge/config.',
        )
    if len(body.patches) > 200:
        raise HTTPException(400, "Patch sequence too long (max 200).")

    patches_dict = [p.model_dump() for p in body.patches]
    try:
        resp = httpx.post(
            f"{_zro_bridge_url}/measure/sequence",
            json={"patches": patches_dict},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            502,
            f"Cannot reach ZRO Bridge at {_zro_bridge_url} — is start.bat running on the Windows PC?",
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"ZRO Bridge error: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"ZRO Bridge proxy error: {exc}")


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
def adb_cms_set(req: _AdbCmsSetReq):
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


@app.post("/api/adb/cms/get")
def adb_cms_get(req: _AdbCmsGetReq):
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
def adb_picture_set(req: _AdbPictureSetReq):
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
def adb_picture_get(req: _AdbPictureGetReq):
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


_zro_bridge_url: str = os.getenv("ZRO_BRIDGE_URL", "http://localhost:7070").rstrip("/")
_ZRO_BRIDGE_TIMEOUT = 5.0


@app.get("/api/zro/bridge/status")
@app.get("/api/bridge/status")
def zro_bridge_status():
    global _zro_bridge_url
    if not _zro_bridge_url:
        return {
            "configured": False,
            "url": None,
            "ok": False,
            "error": "ZRO Bridge URL not configured",
        }
    try:
        resp = httpx.get(f"{_zro_bridge_url}/status", timeout=_ZRO_BRIDGE_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return {"configured": True, "url": _zro_bridge_url, "ok": True, **data}
    except httpx.ConnectError:
        return {
            "configured": True,
            "url": _zro_bridge_url,
            "ok": False,
            "error": "Cannot reach ZRO Bridge â€” is start.bat running on the Windows PC?",
        }
    except Exception as exc:
        return {
            "configured": True,
            "url": _zro_bridge_url,
            "ok": False,
            "error": str(exc),
        }


@app.post("/api/zro/bridge/config")
@app.post("/api/bridge/url")
def zro_bridge_config(body: _ZroBridgeConfigBody):
    global _zro_bridge_url
    _zro_bridge_url = body.url.rstrip("/")
    _save_prefs()
    return {"ok": True, "url": _zro_bridge_url}


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
    _save_prefs()
    return {"ok": True, **_prefs}


@app.post("/api/zro/trigger")
@app.post("/api/bridge/measure")
def zro_trigger():
    global _zro_bridge_url
    if not _zro_bridge_url:
        raise HTTPException(
            400,
            'ZRO Bridge URL not configured.  Set ZRO_BRIDGE_URL env var or POST /api/zro/bridge/config { "url": "http://<windows-pc>:7070" }',
        )
    try:
        resp = httpx.post(f"{_zro_bridge_url}/measure", timeout=_ZRO_BRIDGE_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            502,
            f"Cannot reach ZRO Bridge at {_zro_bridge_url} â€” is start.bat running on the Windows PC?",
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"ZRO Bridge error: {exc.response.text}")
    except Exception as exc:
        raise HTTPException(500, f"ZRO Bridge proxy error: {exc}")


@app.post("/api/session/{sid}/watch")
def session_watch_start(sid: str, body: _WatchStartBody):
    return watch_config(_WatchConfigBody(sid=sid, path=body.path))


@app.post("/api/watch/stop")
def watch_stop():
    return watch_config_delete()


@app.post("/api/watch/config")
def watch_config(body: _WatchConfigBody):
    global _watched_session_id
    sid = body.sid
    session = store.get(sid)
    abs_path = os.path.abspath(body.path)
    csv_parent_exists = abs_path.lower().endswith(".csv") and os.path.isdir(
        os.path.dirname(abs_path) or "."
    )
    if not (os.path.isdir(abs_path) or csv_parent_exists):
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
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _watched_session_id = sid
    return _watch_status_payload()


@app.delete("/api/watch/config")
def watch_config_delete():
    global _watched_session_id
    _fw_stop()
    _watched_session_id = None
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
