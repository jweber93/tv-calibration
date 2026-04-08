#!/usr/bin/env python3
"""
ZRO Calibration Helper â€” web API backend.

Run with:
    uvicorn server:app --host 0.0.0.0 --port 8000
    # or: python server.py
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from calcore.analysis import analyze as _calcore_analyze
from calcore.llm import call_llm as _call_llm
from calcore.models import AnalysisConfig, LLMConfig, Patch
from calcore.phase import determine_phase as _determine_phase
from calibrator import TV_PROFILES
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
    render_report_html as _render_report_html,
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

app = FastAPI(title="ZRO Calibration Helper")

SESSION_STORE_DIR = Path(__file__).parent / ".sessions"
SESSION_TTL = timedelta(days=7)
_watched_session_id: Optional[str] = None

_dogegen_proc: Optional[subprocess.Popen] = None
_dogegen_launch_cmd: List[str] = []
_dogegen_last_error: Optional[str] = None
_dogegen_started_at: Optional[datetime] = None
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


class DogegenConfigReq(BaseModel):
    path: Optional[str] = None
    resolve_host: Optional[str] = None
    window_pct: Optional[int] = None
    maxcll: Optional[int] = None


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
                lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
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
        elapsed = 0.0 if _dogegen_started_at is None else (_now() - _dogegen_started_at).total_seconds()
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
        resolve_arg = f"resolve_hdr {resolve_host} {window_pct}" if resolve_host else f"resolve_hdr {window_pct}"
        cmd.append(resolve_arg)
        return cmd
    return [exe_path]


def _start_dogegen_for_session(session: Dict[str, Any]) -> Dict[str, Any]:
    global _dogegen_proc, _dogegen_launch_cmd, _dogegen_last_error, _dogegen_started_at
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
    status["session_exists"] = bool(_watched_session_id and _watched_session_id in _sessions)
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
) -> None:
    try:
        summary = _calcore_analyze(patches, cfg)
        result = _call_llm(summary, cfg, phase, llm_cfg)
        _llm_broadcast(sid, {"event": "llm_insight", "data": result})
    except Exception as exc:
        _llm_broadcast(sid, {"event": "llm_error", "data": str(exc)})


def _maybe_trigger_llm(sid: str, session: Dict[str, Any]) -> None:
    """Fire a background LLM analysis if the session has LLM configured and measurements."""
    llm_cfg_dict = session.get("llm_config", {})
    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        return

    all_measurements = (
        session.get("pre_measurements", [])
        + session.get("wb_measurements", [])
        + session.get("gamma_measurements", [])
        + session.get("cms_measurements", [])
        + session.get("post_measurements", [])
    )
    if not all_measurements:
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
    threading.Thread(
        target=_run_llm_background,
        args=(sid, patches, cfg, session.get("step", "baseline"), llm_cfg),
        daemon=True,
    ).start()


@app.get("/api/profiles")
def list_profiles():
    return [{"key": key, "name": profile.name} for key, profile in TV_PROFILES.items()]


@app.post("/api/session")
def create_session(req: CreateSessionReq):
    session = store.create_session(req.tv_key, req.sdr_peak_nits)
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


@app.post("/api/session/{sid}/next")
def next_step(sid: str):
    return _session_view(store.next_step(sid, QG_LUMINANCE_PCT))


@app.post("/api/session/{sid}/prev")
def prev_step(sid: str):
    return _session_view(store.prev_step(sid))


@app.post("/api/session/{sid}/llm/configure")
def configure_llm(sid: str, req: LlmConfigureReq):
    session = store.get(sid)
    llm_cfg = session.setdefault("llm_config", {
        "endpoint": "",
        "model": "",
        "api_key": "",
        "temperature": 0.2,
        "timeout": 30.0,
    })
    
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
    
    # Test endpoint reachability if configured
    configured = bool(llm_cfg.get("endpoint") and llm_cfg.get("model"))
    reachable = False
    if configured:
        try:
            # Simple connectivity test - attempt to reach the endpoint
            test_resp = httpx.get(llm_cfg["endpoint"].rstrip("/chat/completions"), timeout=5.0)
            reachable = test_resp.status_code < 500
        except Exception:
            reachable = False
    
    _save_session(sid)
    
    return {
        "configured": configured,
        "reachable": reachable,
        "model": llm_cfg.get("model", ""),
    }


@app.get("/api/session/{sid}/llm/status")
def llm_status(sid: str):
    session = store.get(sid)
    llm_cfg = session.get("llm_config", {})
    configured = bool(llm_cfg.get("endpoint") and llm_cfg.get("model"))
    reachable = False
    
    if configured:
        try:
            test_resp = httpx.get(llm_cfg["endpoint"].rstrip("/chat/completions"), timeout=5.0)
            reachable = test_resp.status_code < 500
        except Exception:
            reachable = False
    
    return {
        "configured": configured,
        "reachable": reachable,
        "model": llm_cfg.get("model", ""),
    }


@app.post("/api/session/{sid}/llm/run")
def llm_run(sid: str):
    session = store.get(sid)
    llm_cfg_dict = session.get("llm_config", {})

    if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
        raise HTTPException(400, "LLM not configured; POST /api/session/{sid}/llm/configure first.")

    all_measurements = (
        session.get("pre_measurements", [])
        + session.get("wb_measurements", [])
        + session.get("gamma_measurements", [])
        + session.get("cms_measurements", [])
        + session.get("post_measurements", [])
    )
    if not all_measurements:
        raise HTTPException(400, "No measurements in session; import data before running LLM analysis.")

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


@app.post("/api/session/{sid}/import/zro")
async def import_zro_csv(sid: str, file: UploadFile = File(...)):
    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(400, f"Could not read uploaded file: {exc}") from exc
    session, import_meta = store.import_zro_bytes(sid, file.filename, contents)
    _maybe_trigger_llm(sid, session)
    return {"session": _session_view(session), "import_summary": import_meta}


@app.post("/api/session/{sid}/import/generic")
async def import_generic_csv(sid: str, file: UploadFile = File(...)):
    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(400, f"Could not read uploaded file: {exc}") from exc
    session, import_meta = store.import_generic_bytes(sid, file.filename, contents)
    _maybe_trigger_llm(sid, session)
    return {"session": _session_view(session), "import_summary": import_meta}


@app.get("/api/session/{sid}/report")
def get_report(sid: str):
    return _report_payload(store.get(sid))


@app.get("/api/session/{sid}/report/html")
def get_report_html(sid: str):
    report = _report_payload(store.get(sid))
    return HTMLResponse(_render_report_html(report))


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
        result = _adb.set_cms_value(channel=req.channel, control=req.control, value=req.value, device=req.device)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(502, f"ADB command failed: {result['stderr'] or result['stdout']}")
    return result


@app.post("/api/adb/cms/get")
def adb_cms_get(req: _AdbCmsGetReq):
    try:
        result = _adb.get_cms_value(channel=req.channel, control=req.control, device=req.device)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(502, f"ADB command failed: {result['stderr'] or result['stdout']}")
    return result


@app.get("/api/adb/cms/all")
def adb_cms_get_all(device: Optional[str] = None):
    try:
        result = _adb.get_all_cms_values(device=device)
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(502, f"ADB command failed: {result['stderr'] or result['stdout']}")
    return result


@app.post("/api/adb/cms/reset")
def adb_cms_reset(device: Optional[str] = None):
    try:
        result = _adb.reset_cms(device=device)
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(502, f"ADB command failed: {result['stderr'] or result['stdout']}")
    return result


@app.post("/api/adb/picture/set")
def adb_picture_set(req: _AdbPictureSetReq):
    try:
        result = _adb.set_picture_control(control=req.control, value=req.value, device=req.device)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ADB error: {exc}") from exc
    if not result["ok"]:
        raise HTTPException(502, f"ADB command failed: {result['stderr'] or result['stdout']}")
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
        raise HTTPException(502, f"ADB command failed: {result['stderr'] or result['stdout']}")
    return result


_zro_bridge_url: str = os.getenv("ZRO_BRIDGE_URL", "http://localhost:7070").rstrip("/")
_ZRO_BRIDGE_TIMEOUT = 5.0


@app.get("/api/zro/bridge/status")
@app.get("/api/bridge/status")
def zro_bridge_status():
    global _zro_bridge_url
    if not _zro_bridge_url:
        return {"configured": False, "url": None, "ok": False, "error": "ZRO Bridge URL not configured"}
    try:
        resp = httpx.get(f"{_zro_bridge_url}/status", timeout=_ZRO_BRIDGE_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return {"configured": True, "url": _zro_bridge_url, "ok": True, **data}
    except httpx.ConnectError:
        return {"configured": True, "url": _zro_bridge_url, "ok": False, "error": "Cannot reach ZRO Bridge â€” is start.bat running on the Windows PC?"}
    except Exception as exc:
        return {"configured": True, "url": _zro_bridge_url, "ok": False, "error": str(exc)}


@app.post("/api/zro/bridge/config")
@app.post("/api/bridge/url")
def zro_bridge_config(body: _ZroBridgeConfigBody):
    global _zro_bridge_url
    _zro_bridge_url = body.url.rstrip("/")
    return {"ok": True, "url": _zro_bridge_url}


@app.post("/api/zro/trigger")
@app.post("/api/bridge/measure")
def zro_trigger():
    global _zro_bridge_url
    if not _zro_bridge_url:
        raise HTTPException(400, 'ZRO Bridge URL not configured.  Set ZRO_BRIDGE_URL env var or POST /api/zro/bridge/config { "url": "http://<windows-pc>:7070" }')
    try:
        resp = httpx.post(f"{_zro_bridge_url}/measure", timeout=_ZRO_BRIDGE_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(502, f"Cannot reach ZRO Bridge at {_zro_bridge_url} â€” is start.bat running on the Windows PC?")
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
    csv_parent_exists = abs_path.lower().endswith(".csv") and os.path.isdir(os.path.dirname(abs_path) or ".")
    if not (os.path.isdir(abs_path) or csv_parent_exists):
        raise HTTPException(400, f"Watch path does not exist: {body.path!r}")
    try:
        _fw_start(
            body.path,
            lambda: _sessions.get(sid),
            lambda: _save_session(sid),
            measurement_deserializer=_deserialize_measurement,
            grayscale_level_count=len(_grayscale_levels_for_ramp(session.get("grayscale_ramp_steps", 11))),
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
