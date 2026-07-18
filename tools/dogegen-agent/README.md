# Dogegen Companion Agent

A small HTTP service that runs on the Windows PC next to Dogegen and exposes
`GET /status`, `POST /start`, `POST /stop`, and `POST /patch` so a backend
running anywhere on the LAN — including in a Docker container on a different
machine — can control Dogegen without needing local process access to the PC
it runs on.

This mirrors the [ZRO Bridge](../zro-bridge/) pattern (`bridge.py`), applied
to Dogegen's process-control logic that previously lived only in `server.py`
as direct `subprocess.Popen` calls.

`POST /patch` (#630) drives Dogegen directly over Light Illusion's public
"Resolve" pattern protocol — the same one ColourSpace uses — so the app can
show an arbitrary RGB patch on the TV with **no ColourSpace/ZRO license
running at all**. See [Direct-drive Dogegen](#direct-drive-dogegen-no-colourspace)
below.

## Install & run (Windows)

### Option A: prebuilt executable (recommended, no Python required)

This agent ships as part of the combined
**[Windows Companion](../windows-companion-tools/)** executable — one
`companion.exe` that runs this agent alongside the [ZRO Bridge](../zro-bridge/)
in a single process (pass `--skip-bridge` if this PC isn't also handling
meter measurements). See [that README](../windows-companion-tools/README.md)
for download/run instructions.

Once running, confirm this agent specifically is up:

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

If you're running this agent via the prebuilt `companion.exe`, see
[Windows Companion: Running persistently](../windows-companion-tools/README.md#running-persistently-surviving-reboot)
— it covers registering the combined executable as an NSSM service or
Task Scheduler task.

If you're running `agent.py` standalone from source (not via
`companion.py`), the same idea applies to just this service:

* **[NSSM](https://nssm.cc/)** (Non-Sucking Service Manager) — the simplest
  option. `nssm install DogegenAgent "C:\Path\To\python.exe" "C:\Path\To\tools\dogegen-agent\agent.py" --config "C:\Path\To\tools\dogegen-agent\agent.json"`,
  then `nssm start DogegenAgent`. NSSM restarts it automatically if it
  crashes and it starts on boot like any other Windows service.
* **Task Scheduler** — create a task triggered "At log on" (or "At startup"
  for a service-like account), action = run `python.exe` with the same
  arguments as above, and "Run whether user is logged on or not" if you
  want it up before anyone signs in.

Either way, point the action/task at `agent.py` directly (not `start.bat`)
so it doesn't sit waiting at the `pause` prompt at the end of the script.

## Configuration

Edit `agent.json` (created from `agent.example.json` on first run):

| Field | Description |
|---|---|
| `host` | Bind address. `0.0.0.0` (default) accepts LAN connections; set to `127.0.0.1` to restrict to this PC only. |
| `port` | Port to listen on (default `7071`). |
| `path` | Full path to `Dogegen.exe`. Leave `""` to auto-detect — checks `path`, then `tools/dogegen/Dogegen.exe` next to this repo checkout, then `Dogegen.exe`/`dogegen.exe` on `PATH`. |
| `resolve_host` | Hostname/IP passed to Dogegen's `resolve_hdr`/`resolve_sdr` launch args. Leave `""` (default) for direct-drive — see below. Set this only to point Dogegen at a remote ColourSpace/other Resolve-protocol host instead. |
| `resolve_listen_port` | Port this agent's own direct-drive Resolve server listens on (default `20002`, Dogegen's own built-in default). Ignored when `resolve_host` is set to a remote target. |
| `window_pct` | HDR window size, percent of screen, passed to `resolve_hdr`. |
| `maxcll` | MaxCLL value passed to Dogegen for HDR10 patterns. |
| `ready_delay_seconds` | Seconds after launch before Dogegen is reported `ready` in `/status`. |

## Trust model

There is no authentication in v1 — the same trust model as the ZRO Bridge.
Anyone who can reach the agent's port can start/stop Dogegen on this PC, or
(with `POST /patch`) change what it's displaying. This is meant to run on a
**trusted LAN only**. If the backend always runs on this same PC and you
want to disable remote access entirely, set `"host": "127.0.0.1"` in
`agent.json`.

### Security implications

`POST /patch` (#630) is the highest-impact endpoint here to expose
carelessly: unlike `/start`/`/stop`, which only toggle a known process,
`/patch` lets any caller who can reach this port display **arbitrary
content** on whatever screen Dogegen is driving, on demand, for as long as
Dogegen stays connected — for review purposes, this is functionally
"remote framebuffer write," not just remote control of a known app.

- **Single-machine / trusted-LAN deployments** (the common case): set
  `"host": "127.0.0.1"` in `agent.json` so the agent only accepts
  connections from the same PC, and keep it off any network that isn't
  fully trusted.
- **Multi-host deployments** (backend in Docker/Unraid, agent on a separate
  Windows PC — see [Full split-host](../../README.md#full-split-host-math--ui-in-docker-spectrometer--dogegen-on-a-windows-pc)):
  run the agent behind a reverse proxy or VPN/tunnel (e.g. Tailscale, as
  already suggested for `DOGEGEN_AGENT_URL` elsewhere in this repo's docs)
  that enforces authentication, rather than exposing `0.0.0.0:7071`
  directly to any network wider than your own LAN.
- **Follow-up (not implemented here):** a minimal opt-in bearer-token or
  HMAC-signed-request check, configurable in `agent.json` and disabled by
  default, would let multi-host setups authenticate without a separate
  reverse proxy. Tracking this as a possible enhancement rather than adding
  it speculatively in this PR — the reverse-proxy/VPN options above already
  cover the exposed-beyond-LAN case safely today.

## Direct-drive Dogegen (no ColourSpace)

Dogegen speaks Light Illusion's public "Resolve" pattern protocol — the
same one ColourSpace uses to tell it which patch to show. Dogegen is the
TCP *client* side of that protocol: when launched with
`resolve_hdr`/`resolve_sdr`, it dials out to a host:port (default
`127.0.0.1:20002`) and waits to be told what to display. Historically that
listener has always been ColourSpace; this agent can be that listener
instead, closing the last paid-product dependency in the measurement loop
(see issue #630 — combined with the ArgyllCMS meter backend, #520, the app
can own pattern → measure → compute → tell-user end to end).

With `resolve_host` left at its default (`""`), `POST /start` already
launches Dogegen pointed at `127.0.0.1:<resolve_listen_port>` — this
agent's own built-in Resolve server, which starts automatically the moment
the agent process starts (before `/start` is ever called, so it's already
listening when Dogegen tries to connect). No extra configuration is needed
for the common case:

1. `POST /start {"mode": "HDR10"}` (or `"SDR"`) — launches Dogegen, which
   connects back to this agent.
2. `GET /status` — check `resolve_connected: true` once Dogegen has dialed
   in (usually near-instant after step 1).
3. `POST /patch {"r": 512, "g": 512, "b": 512, "bits": 10}` for each patch
   you want measured — see [`POST /patch`](#post-patch) below.

If `resolve_host` is set (pointing Dogegen at a real ColourSpace instance
instead), `/patch` will report `409` (`not_connected`) since Dogegen isn't
connected to this agent in that mode.

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
  "launch_cmd": [],
  "resolve_listening": true,
  "resolve_connected": false,
  "resolve_peer": null,
  "resolve_listen_port": 20002,
  "resolve_last_error": null
}
```

`ready`/`ready_in_ms` are computed from the agent's own `started_at` for a
process it launched (`managed: true`); a Dogegen instance detected via
`tasklist`/`pgrep` that the agent didn't launch itself is reported as
`running: true`, `managed: false`, `ready: true` immediately.

`resolve_listening`/`resolve_connected`/`resolve_peer`/`resolve_listen_port`/
`resolve_last_error` describe the direct-drive Resolve server (#630, see
above) — independent of `running`/`managed`/`pid`, which describe the
Dogegen *process*. `resolve_listening` is normally always `true` once the
agent has started (it only goes `false` if `resolve_listen_port` couldn't
be bound, e.g. already in use — check `resolve_last_error`).
`resolve_connected` is `true` once a launched Dogegen has dialed back in.

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

### `POST /patch`

Push one RGB patch to Dogegen over the direct-drive Resolve connection
(#630) — see [Direct-drive Dogegen](#direct-drive-dogegen-no-colourspace)
above. Requires Dogegen to already be started and connected
(`resolve_connected: true` in `/status`).

Body:

```json
{
  "r": 512, "g": 512, "b": 512,
  "bits": 10,
  "size_pct": null,
  "x": 0.0, "y": 0.0, "cx": 1.0, "cy": 1.0,
  "bg_r": 0, "bg_g": 0, "bg_b": 0, "bg_bits": null
}
```

Only `r`/`g`/`b` are required; every other field has the default shown
above. `r`/`g`/`b`/`bg_r`/`bg_g`/`bg_b` are raw device-code integers for the
given bit depth — 0-255 at `bits: 8`, 0-1023 at `bits: 10` (`bits` must be
one of those two values) — not normalized 0-1 floats or percentages,
matching the RGB convention used everywhere else in this app.
`x`/`y`/`cx`/`cy` are a fractional (0-1) rectangle; the default
`(0, 0, 1, 1)` is full-field, which lets Dogegen's own `window_pct` (set at
launch, see Configuration above) decide the on-screen box size — the usual
case. Set `size_pct` instead (0-100) for a centered box of that size for
*this patch specifically* — e.g. a smaller window on near-black patches for
flare control — computed with the same formula Dogegen's own `window_pct`
uses; `size_pct` overrides `x`/`y`/`cx`/`cy` when both are given.

Returns `{"ok": true}` on success. On failure, returns a non-2xx status
with a **structured JSON body** (not FastAPI's default `{"detail": "..."}`
string envelope) and no partial state change:

```json
{"ok": false, "error_type": "not_connected", "error": "Dogegen is not connected to the Resolve server. ..."}
```

`error_type` is a stable field to branch on programmatically; `error` is
the human-readable message. No partial state change happens on any
failure.

| HTTP status | `error_type` | Cause |
|---|---|---|
| `400` | `invalid_patch` | Bad `bits` (not 8 or 10) or an `r`/`g`/`b`/`bg_*` value out of range for it. Dogegen's wire protocol has no ack/nack, so this is the only place such a mistake can be caught — an out-of-range value sent anyway would be silently ignored by Dogegen (it keeps showing the previous pattern). |
| `409` | `not_connected` | Dogegen isn't connected to this agent's Resolve server yet (`resolve_connected: false` — start Dogegen first, or check `resolve_host` isn't pointed at a remote ColourSpace instead). |
| `502` | `send_failed` | The connection dropped while sending (Dogegen exited/crashed mid-send). |
| `504` | `timeout` | Sending the patch timed out (2s). |

## Releasing (maintainers)

See [Windows Companion: Releasing](../windows-companion-tools/README.md#releasing-maintainers)
— this agent is built into the combined `companion.exe` published to
GitHub Releases, not as a standalone executable.

## Tests

Unit tests (with `Popen`/`tasklist`/`pgrep` mocked out) live at
[`tests/test_dogegen_agent.py`](../../tests/test_dogegen_agent.py) in the
repo root and run as part of the normal `pytest` suite.
[`tests/test_resolve_protocol.py`](../../tests/test_resolve_protocol.py)
covers the Resolve-protocol codec (`encode_patch_frame`/`decode_patch_frame`)
and `ResolveServer`'s socket handling (real loopback sockets — connect,
send, disconnect, reconnect, timeout) independently of the HTTP layer.
