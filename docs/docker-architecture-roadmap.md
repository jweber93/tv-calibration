# Docker Architecture Roadmap

**Status: shipped.** #584 and #585 landed as `tools/dogegen-agent/` and the
`DOGEGEN_AGENT_URL` backend wiring, respectively. This doc is kept as the
scoping record; for the current user-facing setup see README.md's
["Full split-host"](../README.md#full-split-host-math--ui-in-docker-spectrometer--dogegen-on-a-windows-pc)
section.

Scoping for making Docker a first-class deployment target: split the backend
into a container that can run anywhere on the LAN (Docker Desktop, Unraid) while
Dogegen — a Windows GUI app that needs a real desktop / GPU / HDMI output —
stays on the Windows PC, controlled over the network.

Tracking issues:

- **#584** — Dogegen Companion Agent (Windows-side control service)
- **#585** — Wire the backend to the Companion Agent for split-host deployments

---

## Guiding insight: reuse the ZRO Bridge pattern

The repo already solved this exact problem once. `tools/zro-bridge/bridge.py` is
a small FastAPI/uvicorn service that runs on the Windows PC next to ColourSpace
and exposes `GET /status` / `POST /measure` over HTTP, so a remote backend can
trigger it without local process access. Config lives in `bridge.json`
(`bridge.example.json` template), it launches from `start.bat`, and the backend
addresses it through a single `ZRO_BRIDGE_URL` env var + `.prefs.json`
`bridge_url` field wrapped in a tiny `_ZroBridgeState` (`server.py:344-360`).

Dogegen control is currently local-subprocess-only and cannot survive that same
split:

```
today:   backend ──Popen()/tasklist/pgrep──▶ Dogegen.exe   (must be same OS process table)

target:  backend ──HTTP──▶ Dogegen Companion Agent ──Popen()/tasklist──▶ Dogegen.exe
         (Docker/Unraid)        (Windows PC)
```

So the work is deliberately *unoriginal*: stand up a Dogegen analog of the ZRO
Bridge (#584), then teach the backend to address it exactly the way it already
addresses the ZRO Bridge (#585). When the agent URL is unset, behavior is
byte-for-byte what it is today — same-host installs need zero config changes.

## Model-tier guidance

This roadmap has **no frontier task.** It is network/infra plumbing that follows
an existing, proven pattern in this same repo — not control theory, not color
science, not an error-prone binary wire protocol. The honest recommendation:

- **Frontier (Opus 4.8):** none required. Don't spend it here.
- **Mid (Sonnet 5):** the whole substance — the agent service, the
  process-control port, the backend dispatch branch, the frontend, and every
  test suite. This is a Sonnet roadmap end to end.
- **Cheap / local (Haiku 4.5 or local qwen2.5):** docs, the `agent.json` /
  prefs schema, README + `unraid-template.xml` edits, and pass-through config.

The one task that deserves **extra care within the Sonnet tier** (not a bump to
Opus) is **585b — backend dispatch.** It is a behavior-preserving refactor of a
stateful, lock-guarded subprocess manager: get it subtly wrong and you regress
*every existing same-host install*, not just the new remote path. The mitigation
is test coverage (existing same-host tests must pass unmodified), not a smarter
model.

---

## Item 1 — Dogegen Companion Agent — #584

A lightweight Windows-resident HTTP service that owns the `Popen` / `tasklist`
process logic, so a backend running anywhere on the LAN can start/stop/query
Dogegen over HTTP. Mirrors the `tools/zro-bridge/` layout.

| # | Sub-task | Spec | Definition of Done | Complexity | Model |
|---|---|---|---|---|---|
| 1a | Service skeleton | New `tools/dogegen-agent/`: `agent.py` (FastAPI app), `agent.example.json` (host/port, default Dogegen path, resolve_host, window_pct, maxcll), `start.bat`, `requirements.txt` — mirror the ZRO Bridge file set exactly (`bridge.py`, `bridge.example.json`, `start.bat`, `requirements.txt`). | Runs standalone on Windows via `start.bat`; `GET /status` returns a health payload. | Low | Sonnet 5 |
| 1b | Port control logic | Move `_find_dogegen_executable`, `_dogegen_command_for_session`, `_start_dogegen_for_session`, `_stop_dogegen`, `_external_dogegen_pid`, and the `_dogegen_status_payload` assembly (`server.py:643-790`) into the agent essentially unchanged. Keep the `os.name == "nt"` tasklist / Linux pgrep branch — the agent now *is* the machine with the process table. | `POST /start {mode}`, `POST /stop`, `GET /status` reproduce today's local behavior when called directly against the agent, incl. "already running" (external PID) and "exe not found". | Medium | Sonnet 5 |
| 1c | `/status` payload compatibility | The agent's `GET /status` returns a payload **shape-compatible** with today's `_dogegen_status_payload()` — `running`, `managed`, `ready`, `ready_in_ms`, `pid`, `path`, `configured`, `last_error`, `launch_cmd`, plus `resolve_host` / `window_pct` / `maxcll`. Readiness (`ready` / `ready_in_ms`) is computed agent-side from its own `started_at`. | 585b can proxy the payload through with no reshaping and the existing frontend renders it unchanged. | Low–med | Sonnet 5 |
| 1d | Network / trust model | Document the trust model explicitly (no auth in v1). **Decision:** default bind should match the ZRO Bridge, which binds `0.0.0.0` (`bridge.py:44`) — a loopback-only default makes the agent non-functional for its entire reason to exist (a *remote* backend must reach it). Keep loopback as an opt-in for same-host paranoia. | `agent.json` documents the LAN-trust assumption; bind default is consistent with the ZRO Bridge; loopback opt-in documented. | Low (security judgment — review carefully) | Sonnet 5 |
| 1e | Tests + docs | Unit tests with a faked `Popen` / `tasklist` / `pgrep`; a short `tools/dogegen-agent/README.md` (install/run on Windows). | Tests cover start/stop/status incl. "already running" and "exe not found"; README gets a user running the agent standalone. | Medium (tests) / Low (docs) | Sonnet 5 (tests) · Haiku 4.5 / local (README) |

**No frontier task.** 1b is the most substantive — it's a lift-and-shift of
proven process-control code, including the fiddly `tasklist` CSV parsing
(`server.py:672-683`). Care needed, novelty not. 1d is the only judgment call
worth a slow human/Sonnet read because it's a security-relevant default; note
that the issue text as filed ("loopback-only default … same trusted-LAN
assumption the ZRO Bridge already makes") is internally inconsistent, since the
Bridge actually defaults to `0.0.0.0` — resolve that before implementing.

---

## Item 2 — Wire the backend to the Companion Agent — #585

Teach the backend to address the agent over HTTP when configured, exactly the
way it already addresses the ZRO Bridge. Empty config = today's local-`Popen`
behavior, unchanged.

| # | Sub-task | Spec | Definition of Done | Complexity | Model |
|---|---|---|---|---|---|
| 2a | Config | Add `DOGEGEN_AGENT_URL` env var + `.prefs.json` field + a `_DogegenAgentState` mirroring `_ZroBridgeState` (`server.py:344-360`); add a `/api/dogegen/config` field for the URL. Wire into `_load_prefs` / `_save_prefs` alongside `bridge_url` (`server.py:445,455-462`). | Config loads/persists like `ZRO_BRIDGE_URL`; default empty. | Low | Sonnet 5 |
| 2b | Backend dispatch | When `DOGEGEN_AGENT_URL` is set, `_dogegen_status_payload`, `_start_dogegen_for_session`, `_stop_dogegen` (`server.py:705-790`) **short-circuit the entire local path** and proxy to the agent's `GET /status` / `POST /start` / `POST /stop` — `_find_dogegen_executable` / `_external_dogegen_pid` must not run locally at all (no exe or process exists on the container host). Readiness comes from the agent's payload, not local `_dogegen_state`. When unset, behavior is exactly today's. | Existing same-host tests pass **unmodified**; new tests cover the agent-proxy path (mocked HTTP) incl. "agent unreachable". | Medium–high (behavior-preserving refactor of lock-guarded subprocess manager — regressions hit every existing install) | **Sonnet 5 (highest care)** |
| 2c | Frontend | `DogegenCard.jsx` (`frontend/src/components/DogegenCard.jsx`): add an optional "Agent URL" field alongside path / resolve_host / window_pct / maxcll; the status pill distinguishes "local" vs "remote agent" and surfaces "agent unreachable" the way the Bridge status indicator does today. | User can point the card at a remote agent URL and see live status/start/stop through it. | Medium | Sonnet 5 |
| 2d | Docs | README Docker/Unraid sections (README:196-330): document the split-host topology. **Fix the misleading `unraid-template.xml` path mapping `/app/tools/dogegen → path to Dogegen on host` (README:318)** — a bind-mounted Windows `.exe` cannot be executed from a Linux container; replace it with the `DOGEGEN_AGENT_URL` story. | README + `unraid-template.xml` accurately describe both same-host and split-host setups; no more implying a bind-mounted `.exe` works from a Linux container. | Low | Haiku 4.5 / local |
| 2e | Tests | Mock agent HTTP responses (success, timeout, connection refused) for the 2b proxy path. | Coverage on the new dispatch branch incl. graceful degradation when the agent is offline. | Medium | Sonnet 5 |

**Depends on #584** — 2b needs an agent to call. 2b is the risk center of the
whole effort; everything else is routine wiring that has a working template one
directory over (`_ZroBridgeState`, `/api/zro/bridge/config`, `zro_bridge_status`
at `server.py:2421-2460`).

---

## Scoping review — findings

Both issues are well-formed, correctly sub-tasked, and grounded in accurate line
references. Refinements folded into the tables above:

1. **584 trust-model default is self-contradictory.** It calls for a
   loopback-only default *and* cites "the same trusted-LAN assumption the ZRO
   Bridge already makes" — but the Bridge defaults to `0.0.0.0` (`bridge.py:44`),
   and a loopback default defeats the split-host purpose. Recommendation: default
   to a LAN bind for consistency and usefulness; loopback opt-in. (Item 1d.)
2. **584 needs an explicit `/status` payload-compatibility contract** (new item
   1c) so 585's proxy is a drop-in and the existing frontend keeps working.
3. **584 layout was missing `requirements.txt`** — the ZRO Bridge ships one; the
   agent needs one too. (Item 1a.)
4. **585b must short-circuit the whole payload assembly**, not wrap individual
   subprocess calls — `_find_dogegen_executable` / `_external_dogegen_pid` have no
   meaning on the container host, and readiness must be sourced from the agent.
   (Item 2b.)

None of these change the two-issue split, which is correct: agent first (#584),
then backend wiring (#585).

---

## Recommended sequencing

1. **1a → 1b → 1c** — stand up the agent and prove it start/stop/status against
   Dogegen directly, with a payload the backend can consume unchanged.
2. **1d → 1e** — lock the trust-model default and cover it with tests/docs.
3. **2a → 2b → 2e** — add config, then the dispatch branch, guarded by the
   existing same-host tests staying green plus new mocked-HTTP tests.
4. **2c → 2d** — frontend surface and the README / `unraid-template.xml`
   correction, last, once the path works end to end.
