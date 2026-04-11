# Calibration Helper

**A guided, step-by-step TV calibration assistant built around ColourSpace ZRO.**

Calibration Helper walks you through a full display calibration — from baseline grayscale to white balance, gamma, and color management — importing your ZRO measurements automatically and telling you exactly what to turn and by how much at every step. When you're done, it generates a polished PDF report showing before/after results.

---

## What It Does

Professional TV calibration involves a dozen distinct measurement phases, hundreds of adjustments, and a lot of back-and-forth between your measurement software and your TV's service menu. Calibration Helper replaces the spreadsheets and mental bookkeeping with a structured web app that:

- **Guides you through each phase in order** — Pre-Grayscale → Luminance → White Balance → Gamma → Color Tuner → Post-Grayscale → Report
- **Imports measurements automatically** — point it at your ColourSpace ZRO export folder and it picks up every CSV the moment ZRO writes it
- **Computes exactly what to adjust** — for white balance, it tells you which Gain/Offset knob to move and by how much; for the color tuner it targets hue, saturation, and brightness per primary
- **Tracks quality gates** — flags when ΔE, CCT, or gamma are out of acceptable range so you know when a phase is done vs. when to keep adjusting
- **Exports a full PDF report** — stat cards, pre/post ΔE charts, gamma curve, and measurement tables in a single shareable document

It supports two hardware paths out of the box — **Dogegen** (PC-based HDR10 pattern generator) and **LightSpace Connect** (Apple TV) — and can drive some TVs directly over ADB.

---

## Screenshots

> Run the app, open [http://localhost:8000](http://localhost:8000), and create a session to see the full workflow.

---

## Calibration Workflow

Each session moves through nine sequential steps. You can jump back to any completed step by clicking it in the progress bar.

| Step | What Happens |
|---|---|
| **Select Mode** | Choose SDR, HDR10, or Dolby Vision. Sets the target EOTF, color gamut, and peak luminance. |
| **Prepare** | TV-specific checklist: picture mode to select, settings to reset, service menu notes. Pattern generator and measurement settings configured here. |
| **Pre-Grayscale** | Baseline 0–100% gray ramp. Establishes your starting ΔE and gamma before any adjustments. |
| **Luminance** | Set black level, white clipping, and peak brightness. Guided by a luminance quality gate. |
| **White Balance** | Two-point white balance using 80% (Gain) and 30% (Offset) patches. Gives you per-channel adjustment directions. |
| **Gamma** | Quick (4-point) or fine (21-point) gamma tracking. Shows effective gamma deviation from target at each stimulus step. |
| **Color Tuner** | CMS adjustments for all six primaries and secondaries (Red, Green, Blue, Cyan, Magenta, Yellow). |
| **Post-Grayscale** | Verification ramp after all adjustments. Compares to the pre-cal baseline. |
| **Report** | Full calibration summary with improvement percentage, downloadable as PDF or HTML. |

---

## Requirements

### Software

- **Python 3.11+**
- **ColourSpace ZRO** or **ColourSpace Zero** (measurement software) — [LightIllusion](https://www.lightillusion.com/)
- A modern browser (Chrome or Edge recommended)

### Hardware

One of the following pattern generator setups:

| Generator | Description |
|---|---|
| **Dogegen** | PC application that generates HDR10 test patterns over HDMI. Recommended for HDR10 calibration on Windows. |
| **LightSpace Connect** | Apple TV app that sends patches from ColourSpace. Free tier supports up to 25 patches; paid tier is unlimited. |

A colorimeter or spectrophotometer supported by ColourSpace ZRO (i1Display, Colorimetry Research, Klein K10-A, etc.).

---

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/jweber93/tv-calibration.git
cd tv-calibration
```

### 2. Create a Virtual Environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> **WeasyPrint on Windows** — PDF export uses WeasyPrint 60+, which bundles its own rendering libraries and does not require a separate GTK install. `pip install weasyprint` is sufficient.

### 4. Start the Server

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open **[http://localhost:8000](http://localhost:8000)** in your browser.

The server can also be bound to `localhost` only if you don't need LAN access:

```bash
uvicorn server:app --port 8000
```

---

## Quick Start

1. **Start the server** and open [http://localhost:8000](http://localhost:8000)
2. **Create a session** — enter your TV model name and select a mode (SDR, HDR10, or Dolby Vision)
3. **Work through Prepare** — select your pattern generator, configure measurement settings, and follow the TV checklist
4. **Start the Watch Folder** — point it at your ColourSpace export directory so measurements import automatically
5. **Measure and advance** — each step tells you what to measure in ColourSpace; once the CSV lands, the app imports it and evaluates your results
6. **Follow the guidance** — at White Balance and Color Tuner steps, the app calculates adjustments and tells you what to change
7. **Finish at Report** — download your PDF

---

## Feature Details

### Automatic CSV Import

The Watch Folder monitors a directory for new ColourSpace ZRO `.csv` files. When ZRO finishes a measurement sequence and writes the file, the app detects it within seconds and imports it into the active session step — no manual upload required.

You can also drag-and-drop a CSV onto the upload zone or use the **Measure** button with the ZRO Bridge to trigger a measurement from within the app.

Configure the watch path on the Prepare page. The path persists across sessions.

### ZRO Bridge

The ZRO Bridge lets ColourSpace ZRO trigger a measurement from within the app workflow. Set up a post-measurement webhook in ZRO pointing to `http://localhost:7070/measure` and the app's **Measure** button will fire ZRO's next patch sequence on click.

The Bridge status indicator in the header bar shows whether the connection is live (green) or unreachable (red).

### Dogegen Integration

When Dogegen is selected as the pattern generator, the app manages the Dogegen process directly:

- Starts and stops Dogegen automatically when you enter and leave measurement steps
- Configures the recommended settings for HDR10: ST2084 Rec.2020, 10 bpc, RGB 4:4:4 Full, 10% window
- Shows a status indicator in the header bar (green when running and ready)

Configure the Dogegen executable path and signal settings in the **Dogegen Companion** card on the Prepare page.

### LightSpace Connect

When LightSpace Connect is selected, the app configures the grayscale ramp based on your tier:

- **Free tier** — up to 25 patches; supports 11-step and 21-step ramps
- **Paid tier** — unlimited patches; supports up to 51-step ramps

### AI Assistant (Optional)

Connect any OpenAI-compatible LLM endpoint (Ollama, LiteLLM, OpenAI, Claude via OpenRouter) to receive plain-English calibration coaching after each measurement import.

The assistant system has four integrated capabilities:

| Capability | What It Does |
|---|---|
| **Step guidance** | After each CSV import, the LLM receives a compact measurement summary (scalar ΔE, gamma, PQ error) and the worst-offender patches, then returns one actionable calibration step. |
| **Display memory** | Prior sessions for this TV model are injected into every prompt as a compressed history block (up to 3 sessions + baseline). The LLM can detect drift, thermal aging, and repeat known compromises. |
| **Gamut expert** | `POST /api/session/{sid}/gamut/advise` runs a deterministic gamut feasibility check per primary, then asks the LLM to explain trade-offs in plain English when a primary is outside the ADB CMS correction range. |
| **Patch strategy** | After each import the LLM also recommends which patches to add or skip next (`patch_strategy` SSE event). Strategies with confidence ≥ 0.6 are flagged for auto-apply; lower-confidence ones surface for user review. |

Configure under the **AI Assistant** section on the Prepare page. All AI features are non-blocking — the workflow runs fully without an LLM endpoint configured.

#### LiteLLM Proxy (Recommended)

For model-agnostic routing, response caching, and offline fallback, run the bundled LiteLLM proxy:

```bash
pip install "litellm[proxy]"
litellm --config litellm_config.yaml --port 4000
```

Then set the AI Assistant endpoint to `http://localhost:4000` and model to `tvcal-analyst`.

The proxy routes to Claude Sonnet via OpenRouter by default and falls back to a local Ollama model if the cloud provider is unavailable. Response caching avoids redundant API calls when re-running the LLM on unchanged measurements.

See `litellm_config.yaml` for available models and configuration options.

### ADB TV Control

For supported TVs, the app can apply color management settings directly over Android Debug Bridge without navigating service menus:

- Push CMS values (hue, saturation, brightness per channel) to the TV's DEX interface
- Read and set picture mode, brightness, contrast, and saturation
- Deploy adjustments from the Color Tuner step with a single button click

ADB must be installed and your TV must have ADB debugging enabled over the network. The ADB status card appears on measurement steps when a compatible device is detected.

### PDF and HTML Reports

The Report page offers three export formats:

- **Download PDF** — a paginated PDF with stat cards, measurement tables, and session metadata. Generated server-side via WeasyPrint.
- **Open Full HTML Report** — the same content as an interactive browser page, suitable for sharing or archiving.
- **Download JSON** — the raw report data for use in external tools or scripts.

---

## TV Profiles

TV profiles define the recommended picture mode, reset values, service menu paths, and control ranges for a specific TV model. The app ships with profiles for:

| TV | Modes |
|---|---|
| Hisense U8G | SDR, HDR10, Dolby Vision |
| TCL 6-Series (7105X) | SDR, HDR10 |
| LG OLED B7 | SDR, HDR10 |
| Vizio V-Series (V4K55M) | SDR, HDR10 |

**Adding a profile** — profiles are defined in `calibrator/profiles.py`. Each profile is a Python dataclass specifying the TV name, supported modes, picture mode name, menu navigation paths, and a list of settings to reset, disable, and configure before calibration.

If your TV isn't listed, you can still use the app by creating a session with any TV name — the guidance steps will still run; only the TV-specific checklist content will be absent.

---

## Measurement Settings

| Setting | Options | Notes |
|---|---|---|
| **Grayscale Ramp** | 11-step, 21-step, 51-step | 51-step requires LightSpace Connect paid tier |
| **Signal Range** | Full (0–255 / 0–1023), Limited (16–235 / 64–940) | Match to your pattern generator's output |
| **Patch Code Scale** | 8-bit, 10-bit | Use 10-bit for Dogegen HDR10 workflows |

These are configured on the Prepare page and persist for the session. The Measurement Settings section collapses to show a one-line summary (e.g., *21-step · Full Range · 10-bit*) after setup.

---

## Command-Line Interface

For batch analysis without the web UI:

```bash
python cli.py --csv /path/to/measurements.csv
```

Options:

| Flag | Description |
|---|---|
| `--mode sdr\|hdr` | Calibration mode |
| `--eotf pq\|gamma22\|bt1886\|<value>` | Target EOTF |
| `--target-space bt709\|p3d65\|bt2020` | Target color gamut |
| `--watch` | Watch for new CSV files and re-analyze on change |

---

## Project Structure

```
calibrator/
  session.py          Session state machine and step logic
  profiles.py         TV model profile definitions
  guidance.py         Adjustment computation (WB, gamma, CMS)
  history.py          Per-display calibration history store (sessions.jsonl)
  quality.py          Quality gate thresholds and evaluation
  zro_import.py       ColourSpace ZRO CSV parser and classifier
  file_watcher.py     Watch folder / auto-import
  adb_control.py      Android Debug Bridge integration
  reports.py          HTML and PDF report rendering
  models.py           Report dataclasses

calcore/
  models.py           Measurement, CalibrationTarget, Patch dataclasses
  analysis.py         ΔE 2000, gamma, PQ EOTF calculations
  colour.py           CIE conversions, CCT, primary chromaticities
  gamut.py            Per-primary gamut feasibility check (PrimaryConstraint, GamutDiagnosis)
  phase.py            Calibration phase determination logic
  llm.py              LLM client — guidance, history injection, patch strategy, remediation

server.py             FastAPI application (REST + SSE)
cli.py                Command-line batch analysis entry point
litellm_config.yaml   LiteLLM proxy config (model routing, caching, offline fallback)
frontend/             React + Vite source (built output in static/)
tests/                API, unit, and integration tests
tools/                Reference CSV sequences for ZRO workflows

.calibration-history/ Per-TV session history (auto-created; gitignored)
  {tv_key}/
    sessions.jsonl    One JSON line per completed calibration
    baseline.json     First-ever calibration reference
```

---

## Development

### Running the Frontend Dev Server

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies API requests to `http://localhost:8000` and hot-reloads on file changes.

### Building the Frontend

```bash
cd frontend
npm run build
```

Output goes to `static/`, which the FastAPI server serves directly.

### Running Tests

```bash
pytest -q
```

Targeted test runs:

```bash
pytest -q tests/test_calcore        # Color math unit tests
pytest -q tests/test_server_api.py  # API integration tests
python -m compileall calcore calibrator server.py cli.py
```

---

## AI Integration Architecture

The AI system is structured as three tiers:

```
┌─────────────────────────────────────────────────────────┐
│                  tv-calibration server                  │
│                                                         │
│  CSV import → _calcore_analyze() → Summary              │
│                    │                                    │
│                    ├─ scalar metrics (ΔE, gamma, PQ)    │
│                    ├─ top_offenders (worst 3 patches)   │  ← P0: prompt
│                    └─ PRE-COMPUTED guidance context     │     leakiness fix
│                                                         │
│  calibrator/history.py                                  │
│    load_history(tv_key) → compressed history block      │  ← P1/P2: display
│                    │                                    │     memory
│                    ▼                                    │
│  calcore/llm.py                                         │
│    call_llm(summary, cfg, phase,                        │
│             history_block=...)        → step guidance   │  ← SSE: llm_insight
│    query_next_patch_strategy(...)     → patch sequence  │  ← SSE: patch_strategy
│    query_gamut_advice(diagnosis_text) → trade-off prose │  ← POST gamut/advise
│    query_remediation(event_type, ...) → recovery steps  │  ← POST hw/remediate
│                                                         │
│  calcore/gamut.py                                       │
│    assess_gamut_constraints(color_rows) → GamutDiagnosis│  ← P3: expert-in-loop
│    format_gamut_diagnosis() → text for LLM              │
└─────────────────────────────────────────────────────────┘
              │
              ▼ HTTP POST /v1/chat/completions
┌─────────────────────────────────────────────────────────┐
│           LiteLLM proxy  (localhost:4000)               │
│                                                         │
│  • Response cache (TTL 1h) — avoids duplicate API calls │
│  • Routes: OpenRouter → Claude / GPT-4o / local Ollama  │
│  • Fallback: cloud → local Ollama on network failure    │
└─────────────────────────────────────────────────────────┘
              │
              ▼
    Upstream model (Claude Sonnet / GPT-4o / Qwen local)
```

### AI Data Flow: What Gets Sent to the LLM

The LLM prompt contains **only pre-aggregated data** — no raw per-patch XYZ arrays:

| Data sent | Source | Why |
|---|---|---|
| `grayscale_avg_de`, `grayscale_max_de`, `grayscale_over_3` | Summary scalars | Trend judgment |
| `gamma_midtones`, `pq_err_midtones` | Summary scalars | EOTF diagnosis |
| `color_75/100_avg_de`, `color_75/100_chroma_avg` | Summary scalars | Gamut diagnosis |
| `top_offenders` (worst 3 patches, label + ΔE only) | Derived from rows | Actionable specifics |
| `DISPLAY HISTORY` block | `.calibration-history/` | Drift / aging context |
| Phase, mode, EOTF, target space | Session config | Framing |

**Not sent:** full `grayscale_rows`, full `color_rows` (raw XYZ triples). These exist in the `Summary` for UI rendering but are excluded from the LLM payload.

### New API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/session/{sid}/gamut/diagnosis` | Per-primary gamut constraint report (deterministic) |
| `POST` | `/api/session/{sid}/gamut/advise` | LLM trade-off advice for out-of-range primaries |
| `GET` | `/api/session/{sid}/history` | Calibration history for this TV model |
| `POST` | `/api/session/{sid}/hardware/remediate` | LLM-guided hardware fault recovery plan |

SSE stream (`GET /api/session/{sid}/llm/stream`) now emits two event types:

| Event | Description |
|---|---|
| `llm_insight` | One-step calibration guidance (existing) |
| `patch_strategy` | Recommended patch additions/skips for next pass; `auto_apply: true` if confidence ≥ 0.6 |

---

## Glossary

| Term | Meaning |
|---|---|
| **ΔE (Delta E)** | Perceptual color difference. ΔE < 2 is invisible to most viewers; < 1 is considered excellent. |
| **CCT** | Correlated Color Temperature in Kelvin. The D65 standard white point is 6504 K. |
| **EOTF** | Electro-Optical Transfer Function — the curve that maps signal values to screen brightness. BT.1886 targets γ ≈ 2.40; PQ is used for HDR10. |
| **Grayscale Ramp** | A sweep of gray patches from 0% to 100% stimulus, used to evaluate gamma and white point tracking. |
| **Signal Range** | Whether patch codes use the full 0–255 (or 0–1023) range or the limited 16–235 (64–940) video range. |
| **CMS / Color Tuner** | Color Management System — per-primary adjustments for hue, saturation, and luminance of the six color primaries and secondaries. |
| **ZRO** | ColourSpace ZRO / Zero — the measurement software by LightIllusion that drives colorimeters and exports CSV data. |
| **Dogegen** | A Windows pattern generator application that sends HDR10 test patches over HDMI. |
| **LightSpace Connect** | An Apple TV app that pairs with ColourSpace to generate patches via an Apple TV connected to the display. |
