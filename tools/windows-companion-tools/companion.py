#!/usr/bin/env python3
"""
TV Calibration Windows Companion — single-process launcher that runs the
Dogegen Companion Agent (../dogegen-agent/agent.py) and the ZRO Bridge
(../zro-bridge/bridge.py) together in one executable.

Ports and HTTP APIs are unchanged from running agent.py/bridge.py
separately — dogegen-agent still listens on 7071, zro-bridge still listens
on 7070 (both configurable via their own config files). This just means a
split-host setup (backend in Docker elsewhere, this Windows PC owning the
display + meter) only needs one process/executable running instead of two.

Usage:
    companion.py                                    # both services, default configs
    companion.py --agent-config agent.json --bridge-config bridge.json
    companion.py --skip-agent                        # zro-bridge only
    companion.py --skip-bridge                        # dogegen-agent only

Config files default to agent.json / bridge.json next to this script (or,
when running the packaged .exe, next to the .exe) — copy agent.example.json
/ bridge.example.json to those names to customize settings. If neither
config file is present, both services run with their built-in defaults.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_HERE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent

sys.path.insert(0, str((_HERE / ".." / "dogegen-agent").resolve()))
sys.path.insert(0, str((_HERE / ".." / "zro-bridge").resolve()))

import agent as dogegen_agent  # noqa: E402
import bridge as zro_bridge  # noqa: E402
import uvicorn  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _serve(name: str, config: uvicorn.Config) -> None:
    server = uvicorn.Server(config)
    logger.info("Starting %s on %s:%s", name, config.host, config.port)
    await server.serve()


async def _run(args: argparse.Namespace) -> None:
    tasks = []

    if not args.skip_agent:
        dogegen_agent._config = dogegen_agent.load_config(args.agent_config)
        cfg = uvicorn.Config(
            dogegen_agent.app,
            host=dogegen_agent._config["host"],
            port=dogegen_agent._config["port"],
            log_level="warning",
        )
        cfg.install_signal_handlers = False
        tasks.append(_serve("dogegen-agent", cfg))

    if not args.skip_bridge:
        zro_bridge._config = zro_bridge.load_config(args.bridge_config)
        cfg = uvicorn.Config(
            zro_bridge.app,
            host=zro_bridge._config["host"],
            port=zro_bridge._config["port"],
            log_level="warning",
        )
        cfg.install_signal_handlers = False
        tasks.append(_serve("zro-bridge", cfg))

    if not tasks:
        logger.error("Both --skip-agent and --skip-bridge given — nothing to run.")
        sys.exit(1)

    await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TV Calibration Windows Companion (dogegen-agent + zro-bridge in one process)"
    )
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=_HERE / "agent.json",
        help="Path to agent.json config file (default: agent.json next to this executable)",
    )
    parser.add_argument(
        "--bridge-config",
        type=Path,
        default=_HERE / "bridge.json",
        help="Path to bridge.json config file (default: bridge.json next to this executable)",
    )
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Don't start the Dogegen Companion Agent (port 7071)",
    )
    parser.add_argument(
        "--skip-bridge",
        action="store_true",
        help="Don't start the ZRO Bridge (port 7070)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
