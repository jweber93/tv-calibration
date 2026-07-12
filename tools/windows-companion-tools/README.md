# TV Calibration Windows Companion

One process/executable that runs both Windows companion services for a
**split-host** setup — the calibration backend runs in Docker somewhere
else (e.g. Unraid), while this Windows PC owns the display connection and
the USB meter:

| Service | Port | Purpose |
|---|---|---|
| [Dogegen Companion Agent](../dogegen-agent/) (`agent.py`) | `7071` | Starts/stops/reports status for **Dogegen**, the HDR10/SDR test-pattern generator. |
| [ZRO Bridge](../zro-bridge/) (`bridge.py`) | `7070` | Triggers and reads measurements from your **meter**, either via ColourSpace ZRO or directly via ArgyllCMS. |

`companion.py` (this folder) is a thin launcher — it imports both services
unmodified and runs them concurrently in one process. Ports, config file
formats, and HTTP APIs are all identical to running `agent.py`/`bridge.py`
separately; see each linked README for configuration fields and API
reference. Pass `--skip-agent` or `--skip-bridge` if this PC only needs one
of the two (e.g. a meter-only PC that doesn't drive the display).

## Install & run (Windows)

### Option A: prebuilt executable (recommended, no Python required)

1. Download `windows-companion-tools.zip` from the
   [Releases page](https://github.com/jweber93/tv-calibration/releases) and
   unzip it into a folder on the Windows PC. It contains
   `companion.exe`, `agent.example.json`, and `bridge.example.json` —
   Python is bundled into the `.exe`, nothing else to install.
2. Copy `agent.example.json` → `agent.json` and `bridge.example.json` →
   `bridge.json` in that same folder, and edit as needed.
3. Double-click `companion.exe`, or run it from a terminal:

   ```
   companion.exe --agent-config agent.json --bridge-config bridge.json
   ```
4. Confirm both are up:

   ```
   curl http://localhost:7071/status   # dogegen-agent
   curl http://localhost:7070/status   # zro-bridge
   ```

### Option B: run from source

1. Install [Python 3.10+](https://python.org) if you don't already have it.
2. Copy this folder *and* its sibling `../dogegen-agent/` and
   `../zro-bridge/` folders to the Windows PC (or clone the whole repo
   there) — `companion.py` imports `agent.py` and `bridge.py` from those
   directories.
3. Double-click `start.bat`. On first run it installs dependencies from
   `requirements.txt` and creates `agent.json`/`bridge.json` from their
   example files.
4. Confirm both are up the same way as above.

Both services listen on `0.0.0.0` by default so a backend on another
machine on your LAN can reach them. Point the backend's `DOGEGEN_AGENT_URL`
at `http://<this-pc-ip>:7071` and `ZRO_BRIDGE_URL` at
`http://<this-pc-ip>:7070`.

### Running persistently (surviving reboot)

Running the `.exe` or `start.bat` directly is meant for manual/on-demand
use — it exits once you close its console window, and it doesn't come back
after a reboot. To keep it running in the background across reboots,
register it as a Windows service or scheduled task instead:

* **[NSSM](https://nssm.cc/)** (Non-Sucking Service Manager) — the simplest
  option.
  * Prebuilt exe: `nssm install TvCalibrationCompanion "C:\Path\To\companion.exe" --agent-config "C:\Path\To\agent.json" --bridge-config "C:\Path\To\bridge.json"`
  * From source: `nssm install TvCalibrationCompanion "C:\Path\To\python.exe" "C:\Path\To\tools\windows-companion-tools\companion.py" --agent-config "C:\Path\To\agent.json" --bridge-config "C:\Path\To\bridge.json"`

  Then `nssm start TvCalibrationCompanion`. NSSM restarts it automatically
  if it crashes and it starts on boot like any other Windows service.
* **Task Scheduler** — create a task triggered "At log on" (or "At startup"
  for a service-like account), action = run `companion.exe` (or
  `python.exe` with the source-install arguments above), and "Run whether
  user is logged on or not" if you want it up before anyone signs in.

Either way, point the action/task at `companion.exe`/`companion.py`
directly (not `start.bat`) so it doesn't sit waiting at the `pause` prompt
at the end of the script.

## Trust model

Neither service authenticates callers in v1 — both are meant for a
**trusted LAN only**. Set `"host": "127.0.0.1"` in the relevant config file
to disable remote access for a service you don't need reachable from other
machines. See each linked README's Trust model section for details.

## Releasing (maintainers)

Pushing a tag matching `companion-tools-v*` (e.g. `companion-tools-v1.0.0`)
triggers the [`windows-companion-tools-release`](../../.github/workflows/windows-companion-tools-release.yml)
GitHub Actions workflow, which builds `companion.exe` with PyInstaller on
`windows-latest` and attaches it (plus both example config files) in
`windows-companion-tools.zip` to a draft GitHub Release for that tag.
Review and publish the draft once it's built. The workflow can also be run
manually (`workflow_dispatch`) to sanity-check the build without cutting a
release.
