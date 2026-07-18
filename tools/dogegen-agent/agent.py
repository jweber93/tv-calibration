#!/usr/bin/env python3
"""
Dogegen Companion Agent — Windows companion service for Dogegen.

Runs on the Windows PC alongside Dogegen. Exposes a small HTTP server so
that a backend running anywhere on the LAN (including in a Docker
container on a separate host) can start/stop/query Dogegen without
needing local process access to the machine Dogegen runs on.

This mirrors the ZRO Bridge (tools/zro-bridge/bridge.py) pattern applied
to Dogegen's process-control logic, previously local-subprocess-only in
server.py.

Usage:
    python agent.py               # reads agent.json from same directory
    python agent.py --config path\to\agent.json
    python agent.py --help

Endpoints:
    GET  /status  — health check; reports Dogegen running/ready/pid/etc.,
                    plus the direct-drive Resolve connection state
    POST /start   — launch Dogegen for a session mode ({"mode": "HDR10"|"SDR"})
    POST /stop    — terminate a Dogegen instance this agent started
    POST /patch   — push one RGB patch straight to Dogegen over the Resolve
                    protocol (#630) — no ColourSpace required

Configure agent.json (see agent.example.json) before starting.

Trust model: like the ZRO Bridge, this service has no authentication and
binds to 0.0.0.0 by default so a remote backend can reach it — it is
meant to run on a trusted LAN only. Set "host" to "127.0.0.1" in
agent.json to restrict it to same-host callers only.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from resolve_protocol import DEFAULT_RESOLVE_PORT, ResolveServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    # Trusted-LAN bind, matching the ZRO Bridge default (bridge.py). Set to
    # "127.0.0.1" here to restrict the agent to same-host callers only.
    "host": "0.0.0.0",
    "port": 7071,
    # Path to Dogegen.exe. Leave empty to auto-detect (see find_dogegen_executable).
    "path": "",
    # Resolve hostname/IP passed to Dogegen's resolve_hdr/resolve_sdr args.
    # Leave empty for direct-drive (#630): Dogegen then falls back to its
    # own built-in 127.0.0.1:20002 default, which is exactly where this
    # agent's own Resolve server (see resolve_listen_port) listens by
    # default — no ColourSpace needed. Set this only to point Dogegen at a
    # remote ColourSpace/other Resolve-protocol host instead.
    "resolve_host": "",
    # Port this agent's own direct-drive Resolve server listens on, for
    # Dogegen to connect to when resolve_host is empty (see above). Not
    # used at all when resolve_host is set to a remote target.
    "resolve_listen_port": DEFAULT_RESOLVE_PORT,
    # HDR window size, percent of screen, passed to resolve_hdr.
    "window_pct": 10,
    # MaxCLL value passed to Dogegen for HDR10 patterns.
    "maxcll": 1000,
    # Seconds after launch before Dogegen is considered "ready" for patterns.
    "ready_delay_seconds": 2.0,
}

_config: dict = dict(DEFAULT_CONFIG)


def load_config(path: Optional[Path] = None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path and path.exists():
        try:
            with open(path) as f:
                overrides = json.load(f)
            cfg.update(overrides)
            logger.info("Loaded config from %s", path)
        except Exception as exc:
            logger.warning("Could not load config %s: %s — using defaults", path, exc)
    else:
        logger.info("No config file found — using defaults (see agent.example.json)")
    return cfg


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Dogegen process state ───────────────────────────────────────────────────

class DogegenState:
    """Tracks a Dogegen process this agent launched itself (as opposed to one
    started outside the agent and merely detected via tasklist/pgrep)."""

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


_dogegen_state = DogegenState()

# Direct-drive Resolve server (#630) — started in main() once config is
# loaded, so it's up before /start ever launches Dogegen (Dogegen dials out
# exactly once, synchronously, when it processes resolve_hdr/resolve_sdr; if
# nothing is listening yet it just logs a failure and never retries).
_resolve_server = ResolveServer()


# ── Dogegen process control ─────────────────────────────────────────────────
#
# Ported from server.py:643-790 (as of the split-host Companion Agent work,
# issue #584/#588) essentially unchanged. server.py's copy still runs today
# for local-subprocess setups; #585 will switch the backend to call this
# agent instead, at which point server.py's copy can be removed. There is no
# shared module between the two, so if server.py's Dogegen launch/detection
# logic changes after this port, re-sync it here by hand.

def find_dogegen_executable(config: dict) -> Optional[str]:
    configured = (config.get("path") or "").strip()
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.append(str(Path(__file__).parent.parent / "dogegen" / "Dogegen.exe"))
    on_path = shutil.which("Dogegen.exe") or shutil.which("dogegen.exe")
    if on_path:
        candidates.append(on_path)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    return None


def external_dogegen_pid() -> Optional[int]:
    """Detect a Dogegen process not launched by this agent (e.g. started by
    hand). Keeps the os.name branch from server.py unchanged — this agent
    *is* the machine with the process table, so it still applies."""
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
                            pid = int(parts[1])
                        except ValueError:
                            continue
                        logger.info("Detected externally-running Dogegen, PID %d", pid)
                        return pid
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
                pid = int(line)
            except ValueError:
                continue
            logger.info("Detected externally-running Dogegen, PID %d", pid)
            return pid
    except Exception:
        return None
    return None


def dogegen_status_payload(config: dict) -> Dict[str, Any]:
    path = find_dogegen_executable(config)
    managed_running = _dogegen_state.is_running()
    external_pid = None if managed_running else external_dogegen_pid()
    running = managed_running or external_pid is not None
    ready = False
    ready_in_ms = 0
    ready_delay = float(config.get("ready_delay_seconds") or DEFAULT_CONFIG["ready_delay_seconds"])
    if external_pid is not None:
        ready = True
    elif managed_running:
        started_at = _dogegen_state.get_started_at()
        elapsed = (
            0.0
            if started_at is None
            else (_now() - started_at).total_seconds()
        )
        ready = elapsed >= ready_delay
        if not ready:
            ready_in_ms = max(0, int((ready_delay - elapsed) * 1000))
    return {
        "configured": bool(path),
        "path": path,
        "running": running,
        "pid": _dogegen_state.get_proc_pid() if managed_running else external_pid,
        "managed": managed_running,
        "ready": ready,
        "ready_in_ms": ready_in_ms,
        "resolve_host": config.get("resolve_host") or "",
        "window_pct": int(config.get("window_pct") or DEFAULT_CONFIG["window_pct"]),
        "maxcll": int(config.get("maxcll") or DEFAULT_CONFIG["maxcll"]),
        "last_error": _dogegen_state.get_last_error(),
        "launch_cmd": _dogegen_state.get_launch_cmd(),
        **_resolve_server.status(),
    }


def _resolve_connect_target(config: dict) -> str:
    """
    The host[:port] token to pass as resolve_hdr/resolve_sdr's IP argument.

    Empty resolve_host (no ColourSpace/other remote target configured)
    lets Dogegen fall back to its own built-in 127.0.0.1:20002 default —
    exactly where this agent's own Resolve server listens by default (see
    resolve_listen_port), so the zero-config direct-drive case (#630) needs
    no host/port on the command line at all. An explicit host:port is only
    added when resolve_host is set (a remote target) or when this agent's
    own listen port has been changed away from Dogegen's built-in default.
    """
    resolve_host = (config.get("resolve_host") or "").strip()
    if resolve_host:
        return resolve_host
    listen_port = int(config.get("resolve_listen_port") or DEFAULT_RESOLVE_PORT)
    if listen_port != DEFAULT_RESOLVE_PORT:
        return f"127.0.0.1:{listen_port}"
    return ""


def dogegen_command_for_mode(mode: Optional[str], exe_path: str, config: dict) -> List[str]:
    window_pct = int(config.get("window_pct") or DEFAULT_CONFIG["window_pct"])
    maxcll = int(config.get("maxcll") or DEFAULT_CONFIG["maxcll"])
    resolve_target = _resolve_connect_target(config)
    if mode == "HDR10":
        cmd = [exe_path, "mode 10_hdr", f"maxcll {maxcll}"]
        resolve_arg = (
            f"resolve_hdr {resolve_target} {window_pct}"
            if resolve_target
            else f"resolve_hdr {window_pct}"
        )
        cmd.append(resolve_arg)
        return cmd
    if mode == "SDR":
        host = resolve_target or "127.0.0.1"
        return [exe_path, f"maxcll {maxcll}", f"resolve_sdr {host}"]
    return [exe_path]


def start_dogegen(mode: Optional[str], config: dict) -> Dict[str, Any]:
    with _dogegen_state._lock:
        if _dogegen_state.is_running() or external_dogegen_pid() is not None:
            return {"ok": True, "already_running": True, **dogegen_status_payload(config)}
        exe_path = find_dogegen_executable(config)
        if not exe_path:
            error = (
                "Dogegen.exe not found. Set 'path' in agent.json, or place it at "
                "tools/dogegen/Dogegen.exe."
            )
            _dogegen_state.set_last_error(error)
            raise HTTPException(400, error)
        cmd = dogegen_command_for_mode(mode, exe_path, config)
        try:
            proc = subprocess.Popen(cmd)
            _dogegen_state.set_started(proc, _now(), cmd)
            return {"ok": True, "already_running": False, **dogegen_status_payload(config)}
        except Exception as exc:
            _dogegen_state.set_failed(cmd, str(exc))
            raise HTTPException(500, f"Failed to start Dogegen: {exc}") from exc


def stop_dogegen(config: dict) -> Dict[str, Any]:
    with _dogegen_state._lock:
        if not _dogegen_state.is_running():
            return {"ok": True, "already_stopped": True, **dogegen_status_payload(config)}
        _dogegen_state.terminate()
        return {"ok": True, "already_stopped": False, **dogegen_status_payload(config)}


# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(title="Dogegen Companion Agent", version="1.0")

# Allow the backend (different origin/host) to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class StartBody(BaseModel):
    mode: Optional[Literal["HDR10", "SDR"]] = None


class PatchBody(BaseModel):
    r: int
    g: int
    b: int
    bits: int = 8
    # Centered window of size_pct% of screen for this patch specifically
    # (e.g. a smaller window on near-black patches for flare control),
    # overriding x/y/cx/cy below. Leave unset to use x/y/cx/cy as given —
    # (0, 0, 1, 1), their default, is full-field and lets Dogegen's own
    # launch-time window_pct decide the on-screen box, same as today.
    size_pct: Optional[float] = None
    x: float = 0.0
    y: float = 0.0
    cx: float = 1.0
    cy: float = 1.0
    bg_r: int = 0
    bg_g: int = 0
    bg_b: int = 0
    bg_bits: Optional[int] = None


# send_patch()'s error_type -> HTTP status. not_connected is 409 (nothing's
# wrong with the request, there's just no Dogegen on the other end yet);
# invalid_patch is a 400 (bad bit depth / out-of-range channel); timeout and
# send_failed are 502/504 the same way the agent-proxy paths in server.py
# map an unreachable agent (see _dogegen_agent_request_error there).
_PATCH_ERROR_STATUS = {
    "invalid_patch": 400,
    "not_connected": 409,
    "timeout": 504,
    "send_failed": 502,
}


@app.post("/patch")
def patch(body: PatchBody):
    """Push one RGB patch to Dogegen over the direct-drive Resolve
    connection (#630) — no ColourSpace involved. Requires Dogegen to have
    already been started (POST /start) and connected back to this agent's
    Resolve server; see GET /status's resolve_connected field.

    Returns send_patch()'s own {"ok": ..., "error_type": ..., "error": ...}
    dict as the JSON body on both success and failure (status code set from
    _PATCH_ERROR_STATUS on failure) rather than FastAPI's default
    {"detail": "..."} envelope, so callers get error_type as a stable,
    parseable field instead of having to pattern-match a message string."""
    result = _resolve_server.send_patch(
        r=body.r,
        g=body.g,
        b=body.b,
        bits=body.bits,
        size_pct=body.size_pct,
        x=body.x,
        y=body.y,
        cx=body.cx,
        cy=body.cy,
        bg=(body.bg_r, body.bg_g, body.bg_b),
        bg_bits=body.bg_bits,
    )
    if not result["ok"]:
        status_code = _PATCH_ERROR_STATUS.get(result.get("error_type"), 502)
        return JSONResponse(status_code=status_code, content=result)
    return result


@app.get("/status")
def status():
    """Health payload, shape-compatible with server.py's _dogegen_status_payload()."""
    return dogegen_status_payload(_config)


@app.post("/start")
def start(body: StartBody):
    return start_dogegen(body.mode, _config)


@app.post("/stop")
def stop():
    return stop_dogegen(_config)


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    global _config

    parser = argparse.ArgumentParser(description="Dogegen Companion Agent — remote start/stop/status")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent / "agent.json",
        help="Path to agent.json config file (default: agent.json next to agent.py)",
    )
    args = parser.parse_args()

    _config = load_config(args.config)

    logger.info("Starting Dogegen Companion Agent on %s:%s", _config["host"], _config["port"])
    logger.info("Dogegen path override: %r", _config.get("path") or "(auto-detect)")

    # Started eagerly (rather than on first /start) so it's already
    # listening before Dogegen ever attempts its one synchronous connect —
    # see _resolve_server's docstring comment above.
    _resolve_server.port = int(_config.get("resolve_listen_port") or DEFAULT_RESOLVE_PORT)
    _resolve_server.start()

    uvicorn.run(app, host=_config["host"], port=_config["port"], log_level="warning")


if __name__ == "__main__":
    main()
