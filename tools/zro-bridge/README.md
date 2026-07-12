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

This bridge ships as part of the combined
**[Windows Companion](../windows-companion-tools/)** executable — one
`companion.exe` that runs this bridge alongside the
[Dogegen Companion Agent](../dogegen-agent/) in a single process (pass
`--skip-agent` if this PC only needs meter measurements). See
[that README](../windows-companion-tools/README.md) for download/run
instructions, and set `"backend"` in `bridge.json` to `"pyautogui"`,
`"remote_control"`, or `"argyll"` before starting it.

Once running, confirm this bridge specifically is up:

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
bundled into `companion.exe` — it ships its own instrument drivers and USB
udev rules that need to be installed the normal way for your meter.

### Running persistently (surviving reboot)

If you're running this bridge via the prebuilt `companion.exe`, see
[Windows Companion: Running persistently](../windows-companion-tools/README.md#running-persistently-surviving-reboot)
— it covers registering the combined executable as an NSSM service or
Task Scheduler task.

If you're running `bridge.py` standalone from source (not via
`companion.py`), the same idea applies to just this service:

* **[NSSM](https://nssm.cc/)** (Non-Sucking Service Manager) — the simplest
  option. `nssm install ZroBridge "C:\Path\To\python.exe" "C:\Path\To\tools\zro-bridge\bridge.py" --config "C:\Path\To\tools\zro-bridge\bridge.json"`,
  then `nssm start ZroBridge`. NSSM restarts it automatically if it crashes
  and it starts on boot like any other Windows service.
* **Task Scheduler** — create a task triggered "At log on" (or "At startup"
  for a service-like account), action = run `python.exe` with the same
  arguments as above, and "Run whether user is logged on or not" if you
  want it up before anyone signs in.

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

See [Windows Companion: Releasing](../windows-companion-tools/README.md#releasing-maintainers)
— this bridge is built into the combined `companion.exe` published to
GitHub Releases, not as a standalone executable.

## Tests

See [`tests/`](../../tests/) in the repo root for the backend unit tests
that run as part of the normal `pytest` suite.
