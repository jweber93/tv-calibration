# Dogegen Companion Agent

A small HTTP service that runs on the Windows PC next to Dogegen and exposes
`GET /status`, `POST /start`, and `POST /stop` so a backend running anywhere
on the LAN — including in a Docker container on a different machine — can
control Dogegen without needing local process access to the PC it runs on.

This mirrors the [ZRO Bridge](../zro-bridge/) pattern (`bridge.py`), applied
to Dogegen's process-control logic that previously lived only in `server.py`
as direct `subprocess.Popen` calls.

## Install & run (Windows)

### Option A: prebuilt executable (recommended, no Python required)

1. Download `dogegen-agent-windows.zip` from the
   [Releases page](https://github.com/jweber93/tv-calibration/releases) and
   unzip it into a folder on the Windows PC that runs Dogegen. It contains
   `dogegen-agent.exe`, `agent.example.json`, and this README — Python
   itself is bundled into the `.exe`, nothing else to install.
2. Double-click `dogegen-agent.exe`. On first run it creates `agent.json`
   from `agent.example.json` next to it.
3. Confirm it's up:

   ```
   curl http://localhost:7071/status
   ```

The agent listens on `0.0.0.0:7071` by default so a backend on another
machine on your LAN can reach it. Point your backend's Dogegen agent URL
setting at `http://<this-pc-ip>:7071`.

### Option B: run from source

1. Install [Python 3.10+](https://python.org) if you don't already have it.
2. Copy this folder (`tools/dogegen-agent/`) to the Windows PC that runs
   Dogegen, or clone the repo there.
3. Double-click `start.bat`. On first run it installs dependencies from
   `requirements.txt` and creates `agent.json` from `agent.example.json`.
4. Confirm it's up:

   ```
   curl http://localhost:7071/status
   ```

The agent listens on `0.0.0.0:7071` by default so a backend on another
machine on your LAN can reach it. Point your backend's Dogegen agent URL
setting at `http://<this-pc-ip>:7071`.

### Running persistently (surviving reboot)

Running the `.exe` or `start.bat` directly is meant for manual/on-demand
use — it exits once you close its console window, and it doesn't come back
after a reboot. To keep the agent running in the background across reboots,
register it as a Windows service or scheduled task instead:

* **[NSSM](https://nssm.cc/)** (Non-Sucking Service Manager) — the simplest
  option.
  * Prebuilt exe: `nssm install DogegenAgent "C:\Path\To\dogegen-agent.exe" --config "C:\Path\To\agent.json"`
  * From source: `nssm install DogegenAgent "C:\Path\To\python.exe" "C:\Path\To\tools\dogegen-agent\agent.py" --config "C:\Path\To\tools\dogegen-agent\agent.json"`

  Then `nssm start DogegenAgent`. NSSM restarts it automatically if it
  crashes and it starts on boot like any other Windows service.
* **Task Scheduler** — create a task triggered "At log on" (or "At startup"
  for a service-like account), action = run `dogegen-agent.exe` (or
  `python.exe` with the source-install arguments above), and "Run whether
  user is logged on or not" if you want it up before anyone signs in.

Either way, point the action/task at `dogegen-agent.exe`/`agent.py` directly
(not `start.bat`) so it doesn't sit waiting at the `pause` prompt at the end
of the script.

## Configuration

Edit `agent.json` (created from `agent.example.json` on first run):

| Field | Description |
|---|---|
| `host` | Bind address. `0.0.0.0` (default) accepts LAN connections; set to `127.0.0.1` to restrict to this PC only. |
| `port` | Port to listen on (default `7071`). |
| `path` | Full path to `Dogegen.exe`. Leave `""` to auto-detect — checks `path`, then `tools/dogegen/Dogegen.exe` next to this repo checkout, then `Dogegen.exe`/`dogegen.exe` on `PATH`. |
| `resolve_host` | Hostname/IP passed to Dogegen's `resolve_hdr`/`resolve_sdr` launch args. |
| `window_pct` | HDR window size, percent of screen, passed to `resolve_hdr`. |
| `maxcll` | MaxCLL value passed to Dogegen for HDR10 patterns. |
| `ready_delay_seconds` | Seconds after launch before Dogegen is reported `ready` in `/status`. |

## Trust model

There is no authentication in v1 — the same trust model as the ZRO Bridge.
Anyone who can reach the agent's port can start/stop Dogegen on this PC.
This is meant to run on a **trusted LAN only**. If the backend always runs
on this same PC and you want to disable remote access entirely, set
`"host": "127.0.0.1"` in `agent.json`.

## API

### `GET /status`

Returns a payload shape-compatible with the backend's existing
`_dogegen_status_payload()`:

```json
{
  "configured": true,
  "path": "C:\\Tools\\Dogegen\\Dogegen.exe",
  "running": false,
  "pid": null,
  "managed": false,
  "ready": false,
  "ready_in_ms": 0,
  "resolve_host": "",
  "window_pct": 10,
  "maxcll": 1000,
  "last_error": null,
  "launch_cmd": []
}
```

`ready`/`ready_in_ms` are computed from the agent's own `started_at` for a
process it launched (`managed: true`); a Dogegen instance detected via
`tasklist`/`pgrep` that the agent didn't launch itself is reported as
`running: true`, `managed: false`, `ready: true` immediately.

### `POST /start`

Body: `{"mode": "HDR10" | "SDR" | null}`. Launches Dogegen with the launch
arguments appropriate to the mode. Any other `mode` value is rejected with
`422` (fails fast on typos like `"Hdr10"`). Returns `{"ok": true,
"already_running": bool, ...status fields}`. Returns `400` if Dogegen can't
be found.

### `POST /stop`

Terminates a Dogegen process this agent started. No-op (`already_stopped:
true`) if nothing is running under agent management. Returns
`{"ok": true, "already_stopped": bool, ...status fields}`.

## Releasing (maintainers)

Pushing a tag matching `dogegen-agent-v*` (e.g. `dogegen-agent-v1.0.0`)
triggers the [`dogegen-agent-release`](../../.github/workflows/dogegen-agent-release.yml)
GitHub Actions workflow, which builds `dogegen-agent.exe` with PyInstaller
on `windows-latest` and attaches `dogegen-agent-windows.zip` to a draft
GitHub Release for that tag. Review and publish the draft once it's built.
The workflow can also be run manually (`workflow_dispatch`) to sanity-check
the build without cutting a release.

## Tests

Unit tests (with `Popen`/`tasklist`/`pgrep` mocked out) live at
[`tests/test_dogegen_agent.py`](../../tests/test_dogegen_agent.py) in the
repo root and run as part of the normal `pytest` suite.
