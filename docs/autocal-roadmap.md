# Autocal Roadmap

Scoping for automated, Calman-style calibration in tv-calibration: closing the
measure → correct → apply → re-measure loop and making the app consumable with
no paid products.

Tracking issues:

- **#519** — Autocal: guided closed-loop measure → correct → apply → re-measure (CMS first)
- **#520** — ArgyllCMS direct-meter backend (license-free measurement, SDR + HDR)
- **#521** — Light Illusion Integration Protocol backend (ColourSpace Servant mode)

---

## Guiding insight: decouple "apply" from "measure"

The autocal loop should not be tied to ADB or to any single TV. The **apply step
is pluggable**:

```
measure → compute correction → APPLY → re-measure → converge
                                 ├── manual: show "set Red Saturation → +3",
                                 │           user changes it on the TV, clicks "Remeasure"   (universal, phase 1)
                                 └── auto:   push via ADB                                     (Hisense U8G, phase 2)
```

The **manual-apply path is the highest-ROI starting point**: it works on *every*
TV, needs no ADB / rooting / per-model integration, and is essentially the
existing guidance engine wrapped in a tight measure/instruct/remeasure loop with
convergence tracking. ADB auto-apply then layers onto the same controller.

Likewise the **measurement source is pluggable** (`measure(patch) -> XYZ`), so the
same controller runs over the ZRO Bridge today and over ArgyllCMS (#520) or the
Integration Protocol (#521) later, unchanged.

## Model-tier guidance

- **Frontier (Opus 4.8):** only where being subtly wrong is costly — the CMS
  controller math (1b) and, conditionally, wire-protocol framing (3a).
- **Mid (Sonnet 5):** the workhorse tier — wiring, endpoints, SSE, Argyll
  integration, UX. Most of this roadmap.
- **Cheap / local (Haiku 4.5 or local qwen2.5):** docs, prefs schema,
  pass-through config only. Do **not** let a local model touch color math or the
  control loop.

---

## Item 1 — CMS Autocal loop (highest ROI) — #519

Guided semi-automatic CMS convergence with a pluggable apply step. Phase 1 =
manual apply (universal); ADB auto-apply is an add-on on the same controller.

| # | Sub-task | Spec | Definition of Done | Complexity | Model |
|---|---|---|---|---|---|
| 1a | Loop abstractions | Define `MeasurementSource.measure(patch)→XYZ` and `ApplyTarget.apply(correction)` (manual + ADB impls). | Interfaces exist with two apply impls; unit-tested with fakes; controller code references no ADB directly. | Low–med | Sonnet 5 |
| 1b | **CMS correction controller** | Map measured per-primary error (Δhue/Δsat/Δluma) → CMS control steps (±10). Proportional + damping; oscillation/overshoot detection; optional 1-step probe to learn per-control gain (secant). | Given a measured error, returns a bounded, damped step vector; converges on a simulated response model without oscillating; never exceeds control range. Tested against a synthetic display model. | **High** (control theory + color science) | **Opus 4.8** |
| 1c | Loop orchestrator | Per-primary iterate measure→correct→apply→remeasure; stop on `quality.py` gate or `max_iterations`; cancel support. | Runs a full 6-primary pass on a simulated source; terminates on both gate and cap; cooperative stop works. | Medium | Sonnet 5 |
| 1d | API + SSE | `POST /autocal/run`, `POST /autocal/stop`; stream per-iteration progress on the existing SSE broadcaster. | Endpoints live; SSE emits iteration ΔE / applied step / state; matches existing SSE patterns. | Low–med | Sonnet 5 |
| 1e | Manual-apply UX | Instruction card ("set Red Saturation → +3"), a **Remeasure** button, live per-primary ΔE progress. | User can run a full manual loop end-to-end in the UI; clear "converged / keep going / out of range" states. | Medium | Sonnet 5 |
| 1f | ADB auto-apply | Wire `ApplyTarget(ADB)` to `/api/adb/cms/set` with read-back verification. | On a U8G, loop applies without user interaction; verifies each write via get; falls back to manual on ADB failure. | Low–med | Sonnet 5 |
| 1g | Docs / prefs | Mode toggle (manual/auto), damping/iteration caps in prefs. | Documented; persisted; sensible defaults. | Low | Haiku 4.5 / local |

**Why 1b is the one frontier task:** the relationship between a CMS
"saturation +1" step and the measured ΔE is nonlinear, per-TV, and unknown a
priori. A naive proportional controller oscillates or stalls. The damping /
secant-gain logic is genuine control-theory + color-science work where mistakes
are subtle and expensive. Everything else follows existing repo patterns.

---

## Item 2 — ArgyllCMS direct-meter backend — #520

Read the colorimeter/spectrophotometer directly via `spotread`, removing
ColourSpace ZRO from the measurement loop. With Dogegen (patterns) + ADB (apply),
the app owns pattern → measure → compute → apply end to end, with **no paid
products**.

### HDR

Works cleanly — ArgyllCMS is EOTF-agnostic measurement transport:

- `spotread -e` (emissive) returns XYZ with **Y in absolute cd/m²** — exactly
  what PQ / ST.2084 targets need.
- HDR **pattern generation stays with Dogegen** (ST.2084 Rec.2020 10-bit);
  Argyll only replaces ZRO's meter-reading role.
- Analysis is already HDR-ready (`calcore/eotf.py`, `analysis.py` compute PQ error).
- Caveat (any meter path): colorimeters need a per-panel spectral correction on
  modern wide-gamut panels (QD-OLED / WOLED / QD-LCD). Argyll supports CCMX/CCSS
  via `spotread -X`. Spectros (i1Pro) don't need it; colorimeters do.

| # | Sub-task | Spec | Definition of Done | Complexity | Model |
|---|---|---|---|---|---|
| 2a | `spotread` wrapper + parse | Single-shot emissive reads (`-e -O`), parse XYZ/xyY across Argyll output variants. | Returns XYZ (Y in cd/m²) for valid reads; structured errors for no-meter/timeout; parser unit-tested on captured outputs. | Medium | Sonnet 5 |
| 2b | Meter discovery / select | Enumerate instruments, surface selection to app. | App lists detected meters; selection persists. | Medium | Sonnet 5 |
| 2c | CCMX/CCSS correction | Pass a correction-file path to `spotread -X`; warn if colorimeter + no correction. | Correction applied when set; warning shown when advisable. | Low | Haiku 4.5 / local |
| 2d | Async execution | Run subprocess off the event loop (matches existing async-I/O fixes). | No event-loop blocking under concurrent reads; verified. | Medium | Sonnet 5 |
| 2e | HDR nit verification | Confirm emissive path reports true absolute nits for PQ. | Test asserts absolute cd/m² (not normalized) at a known luminance. | Medium | Sonnet 5 |
| 2f | Prefs + docs | Backend select (`bridge`/`argyll`), meter type, correction path; install/permissions docs (udev). | Documented; persisted; graceful "Argyll not found". | Low | Haiku 4.5 / local |
| 2g | Tests | Mock `spotread` (success, error, no-meter). | Coverage on parse + error paths. | Medium | Sonnet 5 |

**No frontier task here** — external-CLI integration following known patterns.
Argyll's quirks need care (Sonnet), not novel reasoning. Local models only for
2c/2f.

---

## Item 3 — Light Illusion Integration Protocol backend — #521

Finish the stubbed `remote_control_backend.py` so ColourSpace (Servant mode,
TCP 20102) is fully scriptable — set patch RGB, trigger the probe, read XYZ back.
Same mechanism as JVC AutoCal / ADL4CS. Keeps ZRO; requires a paid license.

| # | Sub-task | Spec | Definition of Done | Complexity | Model |
|---|---|---|---|---|---|
| 3-pre | **Acquire spec** | Get the Integration Protocol PDF/source from Light Illusion (paid license). | Spec in hand. **Blocks everything below.** | — | Human |
| 3a | Protocol framing | Implement `send_command()` encode/decode + connection state machine (TCP 20102). | Round-trips against spec fixtures; reconnects on drop. | **High** (protocol impl, error-prone) | **Opus 4.8** (Sonnet 5 if spec has worked examples) |
| 3b | Patch + measure ops | `set_patch(rgb,size,depth)`, `trigger_measurement()→XYZ`. | Sets patch and returns absolute-nit XYZ against a Servant-mode instance. | Medium | Sonnet 5 |
| 3c | Wire into bridge + fix `/measure/sequence` | Set patch RGB before each measure (fixes today's RGB-ignored behavior in `bridge.py`). | Sequence honors per-patch RGB; conforms to `MeasurementSource`. | Low–med | Sonnet 5 |
| 3d | Docs / tests | Servant-mode setup; framing tests. | Documented; encode/decode tested. | Low–med | Haiku 4.5 → Sonnet 5 |

**Frontier only for 3a**, and only if the spec lacks worked examples. With good
reference source, Sonnet suffices.

---

## Recommended sequencing

1. **1a → 1b → 1c → 1e** (manual-apply CMS loop) — universal, highest ROI; ships
   without ADB or Argyll.
2. **2a–2g** (Argyll) — unlocks the no-paid-products goal; plugs into 1a's
   `MeasurementSource`.
3. **1f** (ADB auto-apply) — cheap add-on once the loop exists.
4. **3** (Integration Protocol) — only after the spec is acquired.
