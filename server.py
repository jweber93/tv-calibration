import asyncio
import json
import logging
import os
import queue
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

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
from calcore.llm import call_llm as _call_llm
from calcore.models import LLMConfig

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

store = SessionStore(
    session_dir_getter=lambda: SESSION_STORE_DIR,
    ttl_getter=lambda: SESSION_TTL,
    watched_session_id_getter=lambda: _watched_session_id,
)
store.load_sessions()

_sessions = store.sessions
_save_session = store.save_session

# SSE: per-session queues for LLM insight events
_sse_queues: Dict[str, List[asyncio.Queue]] = {}

# Thread pool for running synchronous call_llm without blocking the event loop
_llm_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm-worker")


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


def _emit_sse(session_id: str, event_type: str, data: Any) -> None:
    """Push an SSE event to all active stream listeners for a session."""
    payload = json.dumps(data) if not isinstance(data, str) else data
    for q in list(_sse_queues.get(session_id, [])):
        try:
            q.put_nowait({"event": event_type, "data": payload})
        except asyncio.QueueFull:
            pass


async def _run_llm_insight(session_id: str) -> None:
    """Background task: call the LLM and emit the result via SSE."""
    session = _sessions.get(session_id)
    if not session:
        return
    llm_cfg_dict = session.get("llm_config", {})
    endpoint = llm_cfg_dict.get("endpoint", "")
    model = llm_cfg_dict.get("model", "")
    if not endpoint or not model:
        return

    llm_cfg = LLMConfig(
        endpoint=endpoint,
        model=model,
        api_key=llm_cfg_dict.get("api_key", ""),
        temperature=float(llm_cfg_dict.get("temperature", 0.2)),
        timeout=float(llm_cfg_dict.get("timeout", 120.0)),
    )

    # Build a minimal summary dict from session measurements for the prompt
    from calcore.analysis import analyze
    from calcore.models import AnalysisConfig
    from calcore.phase import determine_phase

    pre = session.get("pre_measurements", [])
    post = session.get("post_measurements", [])
    measurements = post if post else pre
    if not measurements:
        _emit_sse(session_id, "llm_insight", {"error": "No measurements available for analysis"})
        return

    try:
        mode = session.get("mode", "SDR")
        eotf = "pq" if mode in ("HDR10", "Dolby Vision") else "bt1886"
        cfg = AnalysisConfig(mode=mode.lower(), eotf=eotf)

        from calcore.csv_import import parse_measurement_csv
        # Use the session's stored measurements directly
        from calcore.models import Patch
        patches = []
        for m in measurements:
            xyz = m.get("meas_xyz") or (m.get("X", 0), m.get("Y", 0), m.get("Z", 0))
            r, g, b = m.get("stimulus_rgb", [0, 0, 0])
            kind = "grayscale" if r == g == b else "color"
            patches.append(Patch(
                label=m.get("label", ""),
                r_target=r,
                g_target=g,
                b_target=b,
                meas_xyz=tuple(xyz),
                kind=kind,
            ))

        summary = analyze(patches, cfg)
        phase = determine_phase(summary, session.get("phase", "baseline"))

        loop = asyncio.get_event_loop()
        insight = await loop.run_in_executor(
            _llm_executor,
            lambda: _call_llm(summary, cfg, phase, llm_cfg),
        )
        if insight:
            _emit_sse(session_id, "llm_insight", {"text": insight, "phase": phase})
        else:
            _emit_sse(session_id, "llm_insight", {"error": "LLM returned no content"})
    except Exception as exc:
        logger.error("LLM insight failed for session %s: %s", session_id, exc)
        _emit_sse(session_id, "llm_insight", {"error": str(exc)})


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
    reachable = False
    if configured:
        try:
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


@app.get("/api/session/{sid}/llm/stream")
async def llm_stream(sid: str, request: Request):
    """SSE endpoint: streams LLM insight events for the given session."""
    store.get(sid)  # raises 404 if not found

    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_queues.setdefault(sid, []).append(q)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield item
                except asyncio.TimeoutError:
                    # Send a keep-alive comment to prevent proxy timeouts
                    yield {"event": "ping", "data": ""}
        finally:
            queues = _sse_queues.get(sid, [])
            if q in queues:
                queues.remove(q)

    return EventSourceResponse(event_generator())


@app.post("/api/session/{sid}/llm/run")
async def llm_run(sid: str):
    """Trigger an LLM insight analysis for the session (result emitted via SSE stream)."""
    store.get(sid)  # raises 404 if not found
    asyncio.create_task(_run_llm_insight(sid))
    return {"ok": True, "message": "LLM insight task started; listen on /llm/stream for results"}


@app.post("/api/session/{sid}/import/zro")
async def import_zro_csv(sid: str, file: UploadFile = File(...)):
    try:
        contents = await file.read()
    except Exception as exc:
        raise HTTPException(400, f"Could not read uploaded file: {exc}") from exc
    session, import_meta = store.import_zro_bytes(sid, file.filename, contents)
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
        return {"configured": True, "url": _zro_bridge_url, "ok": False, "error": "Cannot reach ZRO Bridge — is start.bat running on the Windows PC?"}
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
        raise HTTPException(502, f"Cannot reach ZRO Bridge at {_zro_bridge_url} — is start.bat running on the Windows PC?")
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
    store.get(sid)  # raises 404 if not found

    path = body.path
    _fw_stop()
    _watched_session_id = sid

    try:
        _fw_start(
            path=path,
            session_getter=lambda: _sessions.get(sid),
            session_saver=lambda: _save_session(sid),
            measurement_deserializer=_deserialize_measurement,
        )
    except Exception as exc:
        _watched_session_id = None
        raise HTTPException(400, str(exc)) from exc

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


def watch_events(request: Optional[Request] = None) -> EventSourceResponse:
    """SSE stream for file-watcher import events.

    Can be called directly (without a ``request``) to obtain the
    ``EventSourceResponse`` object for header inspection in tests.
    """
    q: queue.Queue = _fw_subscribe()

    async def generator():
        try:
            while True:
                if request is not None and await request.is_disconnected():
                    break
                try:
                    event = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: _fw_subscribe_poll(q)
                    )
                    if event is not None:
                        yield {"event": "import", "data": json.dumps(event)}
                    else:
                        yield {"event": "ping", "data": ""}
                except Exception:
                    yield {"event": "ping", "data": ""}
                    await asyncio.sleep(1.0)
        finally:
            _fw_unsubscribe(q)

    return EventSourceResponse(generator())


@app.get("/api/watch/events")
async def _watch_events_route(request: Request) -> EventSourceResponse:
    """FastAPI route that delegates to :func:`watch_events`."""
    return watch_events(request)


def _fw_subscribe_poll(q: queue.SimpleQueue) -> Optional[Dict[str, Any]]:
    """Poll the file-watcher event queue with a timeout."""
    try:
        return q.get(timeout=15.0)
    except Exception:
        return None


# Mount static files last so API routes take precedence
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_static_dir / "assets")), name="assets")

    @app.get("/")
    @app.get("/{full_path:path}")
    def serve_spa(full_path: str = ""):
        index = _static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return HTMLResponse("<h1>UI not built</h1>", status_code=200)
