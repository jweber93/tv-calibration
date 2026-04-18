#!/usr/bin/env python3
"""
ZRO Bridge — Windows companion service for the ZRO Calibration Helper.

Runs on the Windows PC alongside ColourSpace ZRO.  Exposes a small HTTP
server so that the Calibration Helper web app can trigger ZRO to take a
probe measurement without the user having to click anything.

Usage:
    python bridge.py               # reads bridge.json from same directory
    python bridge.py --config path\to\bridge.json
    python bridge.py --help

Endpoints:
    GET  /status   — health check; reports whether ZRO window is detected
    POST /measure  — trigger one probe measurement in ZRO

Configure bridge.json (see bridge.example.json) before starting.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 7070,
    # "pyautogui" uses window-focus + keypress/click (no credentials needed).
    # "remote_control" uses the Light Illusion Integration Protocol over TCP
    # (requires the protocol document from Light Illusion Customer Downloads).
    "backend": "pyautogui",

    # ── pyautogui backend settings ──────────────────────────────────────────
    # Partial window title to match (case-insensitive).
    "zro_window_title": "ColourSpace",
    # How to trigger a measurement:
    #   "key"   — bring ZRO to foreground, press the configured key.
    #   "click" — bring ZRO to foreground, click at measure_coords [x, y].
    "measure_action": "key",
    # Key to press when measure_action == "key".
    # Check your ZRO manual/settings for the correct shortcut.
    # Common values: "space", "return", "f5"
    "measure_key": "space",
    # Screen coordinates [x, y] to click when measure_action == "click".
    # Use a tool like WinSpy or inspect ZRO's Measure button position.
    "measure_coords": None,
    # Milliseconds to wait after bringing ZRO to foreground before triggering.
    "pre_trigger_delay_ms": 250,

    # ── remote_control backend settings ────────────────────────────────────
    # Host/port where ColourSpace's Remote Control server is listening.
    # Enable in ZRO: Graph Options → Remote Control → Servant → Local (port 20102).
    "remote_control_host": "127.0.0.1",
    "remote_control_port": 20102,
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
        logger.info("No config file found — using defaults (see bridge.example.json)")
    return cfg


# ── FastAPI app ─────────────────────────────────────────────────────────────────

app = FastAPI(title="ZRO Bridge", version="1.0")

# Allow the Calibration Helper (different origin) to call us.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/status")
def status():
    """Health check — reports whether ZRO window is currently detectable."""
    backend = _config.get("backend", "pyautogui")
    if backend == "pyautogui":
        from backends.pyautogui_backend import find_zro_window
        win = find_zro_window(_config["zro_window_title"])
        return {
            "ok": True,
            "backend": backend,
            "zro_window_found": win is not None,
            "zro_window_title": win.title if win else None,
        }
    elif backend == "remote_control":
        from backends.remote_control_backend import check_connection
        connected = check_connection(
            _config["remote_control_host"],
            _config["remote_control_port"],
        )
        return {
            "ok": True,
            "backend": backend,
            "remote_control_connected": connected,
            "remote_control_host": _config["remote_control_host"],
            "remote_control_port": _config["remote_control_port"],
        }
    else:
        raise HTTPException(400, f"Unknown backend: {backend!r}")


@app.post("/measure")
def trigger_measure():
    """
    Trigger one probe measurement in ZRO.

    Returns immediately after dispatching the trigger.  The web app should
    wait for the ZRO file-watcher SSE event to confirm the CSV was written.
    """
    backend = _config.get("backend", "pyautogui")
    logger.info("Measurement trigger requested (backend=%s)", backend)

    if backend == "pyautogui":
        from backends.pyautogui_backend import trigger_measurement
        result = trigger_measurement(
            window_title=_config["zro_window_title"],
            action=_config["measure_action"],
            key=_config.get("measure_key", "space"),
            coords=_config.get("measure_coords"),
            pre_delay_ms=_config.get("pre_trigger_delay_ms", 250),
        )
        if not result["ok"]:
            raise HTTPException(502, result.get("error", "Trigger failed"))
        return result

    elif backend == "remote_control":
        from backends.remote_control_backend import trigger_measurement
        result = trigger_measurement(
            host=_config["remote_control_host"],
            port=_config["remote_control_port"],
        )
        if not result["ok"]:
            raise HTTPException(502, result.get("error", "Trigger failed"))
        return result

    else:
        raise HTTPException(400, f"Unknown backend: {backend!r}")


@app.post("/measure/sequence")
def trigger_measure_sequence(body: dict):
    """
    Trigger a sequence of arbitrary patch measurements in ZRO.

    Body: {"patches": [{nits, r, g, b, priority, label, rationale}, ...]}

    Sends each patch RGB stimulus to ZRO sequentially, triggering a measurement
    after each one. This supports the LLM-optimized patch density feature where
    patches are not from a fixed grid.

    Returns: {"accepted": int, "results": [...]}
    """
    patches = body.get("patches", [])
    if not patches or not isinstance(patches, list):
        raise HTTPException(400, "Body must include a non-empty 'patches' list.")
    if len(patches) > 200:
        raise HTTPException(400, "Patch sequence too long (max 200).")

    backend = _config.get("backend", "pyautogui")
    logger.info(
        "Measurement sequence requested: %d patches (backend=%s)",
        len(patches),
        backend,
    )

    results = []
    for i, patch in enumerate(patches):
        r = int(patch.get("r", 0))
        g = int(patch.get("g", 0))
        b = int(patch.get("b", 0))
        label = patch.get("label", f"RGB({r},{g},{b})")

        if backend == "pyautogui":
            from backends.pyautogui_backend import trigger_measurement
            result = trigger_measurement(
                window_title=_config["zro_window_title"],
                action=_config["measure_action"],
                key=_config.get("measure_key", "space"),
                coords=_config.get("measure_coords"),
                pre_delay_ms=_config.get("pre_trigger_delay_ms", 250),
            )
        elif backend == "remote_control":
            from backends.remote_control_backend import trigger_measurement
            result = trigger_measurement(
                host=_config["remote_control_host"],
                port=_config["remote_control_port"],
            )
        else:
            result = {"ok": False, "error": f"Unknown backend: {backend!r}"}

        results.append({
            "index": i,
            "label": label,
            "rgb": [r, g, b],
            "ok": result.get("ok", False),
            "error": result.get("error"),
        })

        if not result.get("ok"):
            logger.warning("Patch %d (%s) failed: %s", i, label, result.get("error"))

    return {
        "accepted": len(patches),
        "results": results,
        "succeeded": sum(1 for r in results if r["ok"]),
    }


# ── Entry point ─────────────────────────────────────────────────────────────────

def main():
    global _config

    parser = argparse.ArgumentParser(description="ZRO Bridge — measurement trigger service")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent / "bridge.json",
        help="Path to bridge.json config file (default: bridge.json next to bridge.py)",
    )
    args = parser.parse_args()

    _config = load_config(args.config)

    logger.info("Starting ZRO Bridge on %s:%s (backend=%s)",
                _config["host"], _config["port"], _config["backend"])
    logger.info("ColourSpace window title match: %r", _config["zro_window_title"])

    uvicorn.run(app, host=_config["host"], port=_config["port"], log_level="warning")


if __name__ == "__main__":
    main()
