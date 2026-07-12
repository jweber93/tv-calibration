# ZRO Bridge

A small HTTP service that runs on the Windows PC next to your meter and
exposes `GET /status`, `GET /instruments`, `POST /config/argyll-port`,
`POST /measure`, and `POST /measure/sequence` so a backend running anywhere
on the LAN — including in a Docker container on a different machine — can
trigger and read colorimeter/spectrophotometer measurements without needing
local USB/serial access to the PC the meter is plugged into.

Three backends, selected via `bridge.json`'s `"backend"` field:

| Backend | How it measures |
|---|---|
| `pyautogui` (default) | Brings ColourSpace ZRO to the foreground and sends a keypress/click to trigger its Measure action. |
| `remote_control` | Talks to ColourSpace ZRO's built-in Light Illusion Remote Control (Servant) protocol over TCP. |
| `argyll` | Reads the meter directly via ArgyllCMS's `spotread` — no ZRO/ColourSpace license needed at all. |

## Install & run (Windows)

### Option A: prebuilt executable (recommended, no Python required)

1. Download `windows-companion-tools.zip` from the
   [Releases page](https://github.com/jweber93/tv-calibration/releases) and
   unzip it into a folder on the Windows PC that has the meter plugged in.
   It contains `zro-bridge.exe`, `bridge.example.json`, and (alongside it)
   `dogegen-agent.exe` — Python is bundled into both `.exe`s, nothing else
   to install unless you're using the `argyll` backend (see below).
2. Copy `bridge.example.json` to `bridge.json` in that same folder and set
   `"backend"` to `"pyautogui"`, `"remote_control"`, or `"argyll"`.
3. Double-click `zro-bridge.exe`, or run it from a terminal:

   ```
   zro-bridge.exe --config bridge.json
   ```
4. Confirm it's up:

   ```
   curl http://localhost:7070/status
   ```

### Option B: run from source

1. Install [Python 3.10+](https://python.org) if you don't already have it.
2. Copy this folder (`tools/zro-bridge/`) to the Windows PC, or clone the
   repo there.
3. Double-click `start.bat`. On first run it installs dependencies from
   `requirements.txt` and creates `bridge.json` from `bridge.example.json`.
4. Confirm it's up the same way as above.

The bridge listens on `0.0.0.0:7070` by default so a backend on another
machine on your LAN can reach it. Point your backend's `ZRO_BRIDGE_URL`
setting at `http://<this-pc-ip>:7070`.

### ArgyllCMS direct-meter backend (no paid products)

If you set `"backend": "argyll"`, install [ArgyllCMS](https://www.argyllcms.com/)
separately (free, open source) and make sure `spotread` is on `PATH` or set
`argyll_spotread_path` in `bridge.json` to its full path. ArgyllCMS is not
bundled into `zro-bridge.exe` — it ships its own instrument drivers and USB
udev rules that need to be installed the normal way for your meter.

### Running persistently (surviving reboot)

Running the `.exe` or `start.bat` directly is meant for manual/on-demand
use — it exits once you close its console window, and it doesn't come back
after a reboot. To keep the bridge running in the background across
reboots, register it as a Windows service or scheduled task instead:

* **[NSSM](https://nssm.cc/)** (Non-Sucking Service Manager) — the simplest
  option.
  * Prebuilt exe: `nssm install ZroBridge "C:\Path\To\zro-bridge.exe" --config "C:\Path\To\bridge.json"`
  * From source: `nssm install ZroBridge "C:\Path\To\python.exe" "C:\Path\To\tools\zro-bridge\bridge.py" --config "C:\Path\To\tools\zro-bridge\bridge.json"`

  Then `nssm start ZroBridge`. NSSM restarts it automatically if it crashes
  and it starts on boot like any other Windows service.
* **Task Scheduler** — create a task triggered "At log on" (or "At startup"
  for a service-like account), action = run `zro-bridge.exe` (or
  `python.exe` with the source-install arguments above), and "Run whether
  user is logged on or not" if you want it up before anyone signs in.

## Trust model

There is no authentication in v1. Anyone who can reach the bridge's port
can trigger measurements on this PC. This is meant to run on a **trusted
LAN only**. If the backend always runs on this same PC and you want to
disable remote access entirely, set `"host": "127.0.0.1"` in `bridge.json`.

## API

### `GET /status`

Health check — reports whether the configured backend is reachable (ZRO
window detected, Remote Control connected, or `spotread` resolved).

### `GET /instruments`

`backend=argyll` only. Enumerates meters `spotread` currently detects.

### `POST /config/argyll-port`

`backend=argyll` only. Selects which detected instrument/port to use for
reads (in-memory only, for the life of the running process).

### `POST /measure`

Triggers one probe measurement. For `pyautogui`/`remote_control` this
dispatches the trigger and returns immediately — the calling app watches
for the CSV ZRO exports separately. For `argyll` this reads the meter
directly and returns XYZ synchronously.

### `POST /measure/sequence`

Body: `{"patches": [{nits, r, g, b, priority, label, rationale}, ...]}`
(max 200). Sends each patch's RGB stimulus and triggers a measurement per
patch, sequentially. Returns `{"accepted": int, "results": [...]}`.

## Releasing (maintainers)

Pushing a tag matching `companion-tools-v*` (e.g. `companion-tools-v1.0.0`)
triggers the [`windows-companion-tools-release`](../../.github/workflows/windows-companion-tools-release.yml)
GitHub Actions workflow, which builds both `zro-bridge.exe` and the
[Dogegen Companion Agent](../dogegen-agent/)'s `dogegen-agent.exe` with
PyInstaller on `windows-latest` and attaches them together in
`windows-companion-tools.zip` to a draft GitHub Release for that tag.
Review and publish the draft once it's built. The workflow can also be run
manually (`workflow_dispatch`) to sanity-check the build without cutting a
release.

## Tests

See [`tests/`](../../tests/) in the repo root for the backend unit tests
that run as part of the normal `pytest` suite.
