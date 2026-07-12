# TV Calibration — Windows Companion Tools

Two standalone services for a **split-host** setup: the calibration backend
runs in Docker somewhere else (e.g. Unraid), while this Windows PC owns the
display connection and the USB meter.

| Executable | Port | Purpose |
|---|---|---|
| `dogegen-agent.exe` | `7071` | Starts/stops/reports status for **Dogegen**, the HDR10/SDR test-pattern generator. |
| `zro-bridge.exe` | `7070` | Triggers and reads measurements from your **meter** (colorimeter/spectrophotometer), either via ColourSpace ZRO or directly via ArgyllCMS. |

Both have Python bundled in — no separate Python install needed. Run
whichever ones apply to your setup; you don't need both if, say, you're
only using this PC for the meter.

## Quick start

1. Copy `agent.example.json` → `agent.json` and `bridge.example.json` →
   `bridge.json` in this folder, and edit as needed (see each tool's README
   below for field descriptions).
2. Run `dogegen-agent.exe` and/or `zro-bridge.exe` (double-click, or from a
   terminal so you can see the logs).
3. Confirm each is reachable:
   ```
   curl http://localhost:7071/status   # dogegen-agent
   curl http://localhost:7070/status   # zro-bridge
   ```
4. On the Docker host, point the backend at both:
   ```
   export DOGEGEN_AGENT_URL=http://<this-pc-ip>:7071
   export ZRO_BRIDGE_URL=http://<this-pc-ip>:7070
   ```
   (Both can also be set from the app's Dogegen/Bridge cards, or in
   `.prefs.json`, without restarting the container.)

For the full split-host walkthrough (Windows Firewall, troubleshooting
unreachable agents, etc.) see the [main README's Full split-host
section](https://github.com/jweber93/tv-calibration#full-split-host-math--ui-in-docker-spectrometer--dogegen-on-a-windows-pc).

## Per-tool details

* [`dogegen-agent.README.md`](dogegen-agent.README.md) — configuration
  fields, API reference, persistence (NSSM/Task Scheduler).
* [`zro-bridge.README.md`](zro-bridge.README.md) — backend selection
  (`pyautogui` / `remote_control` / `argyll`), configuration fields, API
  reference, persistence.

## Running persistently (surviving reboot)

Each per-tool README has NSSM/Task Scheduler instructions — point the
service/task at the `.exe` in this folder plus its `--config <path>.json`.

## Trust model

Neither service authenticates callers in v1 — both are meant for a
**trusted LAN only**. Set `"host": "127.0.0.1"` in the relevant config file
to disable remote access for a tool you don't need reachable from other
machines.
