# QE Audit — tv-calibration

Act as a **Staff QE Architect + Color Scientist**. Your goal is a calibration
process that is **fast, deterministic, and error-free** end to end:
Dogegen → U8G → Calibrite → ArgyllCMS → ZRO → TV menu sliders.

Hunt for **real defects** in both the FastAPI backend and the React frontend.
Do not propose features or refactors. For every finding, produce:

1. **Severity** — Critical / High / Medium / Low
2. **Location** — `file:line`
3. **Failure scenario** — concrete inputs/state → wrong output or crash
4. **Why it's wrong** — cite the colorimetric, contract, or UX invariant violated
5. **Fix strategy** — root cause, not symptom
6. **Regression test** — the test that would have caught it (mirror existing
   test patterns: pytest `TestClient` for backend, Vitest+RTL / Playwright for FE)

Rank findings by impact on a real calibration run. A wrong dE2000 or an
inverted slider correction is worse than a cosmetic UI glitch.

---

## Ground truth — these invariants MUST hold

- Target **dE2000 < 2.0**; white point **D65 (6504K)** — but grayscale targets
  use the **session** white point, not a hardcoded D65 (regression: #485).
- **SDR** = BT.1886 EOTF / gamma 2.2; **HDR10** = PQ EOTF. EOTF and gamma are
  **not interchangeable** — confirm neither is silently substituted for the
  other (regression: #487, #388).
- **U8G 20-point WB sliders are sign-inverted**: negative brightens/cools,
  positive darkens/warms. Any correction that flips this sign is a Critical bug.
- Conservative Round-1 correction factor (~0.55) for inter-node bleed.
- Do **not** "correct" the 40–70% firmware tone-mapping wall via menu controls.
- `CalibrationTarget.primaries` must always be normalized to dict form (#362).

---

## Backend track (Python / FastAPI)

Probe these hotspots:

1. **Color math** (`calcore/`, color-science modules): dE2000 near black,
   degenerate EOTF/BT.1886 inputs, division-by-zero / NaN at CV=0, xyY↔XYZ
   round-trips, gamma vs EOTF mixups. Verify against `tests/golden/` datasets
   and `tests/test_colour_*`.
2. **CSV / ZRO import** (`calibrator/zro_import.py`, `csv_adapter.py`):
   headerless XYZ-vs-xyY ambiguity, short/truncated rows, subsecond timestamps,
   multiple grayscale passes, dedup/merge correctness. Off-by-one in pass
   boundaries silently corrupts a run.
3. **Hardware I/O resilience** (`adb_control.py`, `file_watcher.py`, ZRO
   bridge): blocking ArgyllCMS calls, USB timeout retry logic, ADB command
   **injection** safety, file-watcher races. A hang here stalls the whole loop.
4. **LLM query path** (`calcore/llm.py`): per the recipe, `query_*` functions
   must **never raise** — malformed/partial JSON, HTTP errors, empty
   measurements, and not-configured (`endpoint`/`model` empty → `None`) must all
   degrade gracefully, never 500.
5. **Session & concurrency** (`server.py`, `session.py`): concurrent requests
   on the same session id, stale/missing session, state mutation races. The
   endpoint contract must stay stable (typed null, never 500, on unconfigured
   paths).
6. **API contracts**: every endpoint's success and error shape; 400 on bad
   input (budget/param validation), not 500.

## Frontend track (React / Vite)

1. **Numeric & unit correctness** (`utils/fmt.js`, charts, `MeasurementTable`):
   does the UI display the sign-inverted slider values the way an operator must
   *enter* them on the TV? A correct backend value shown with a flipped sign is
   a Critical UX bug.
2. **State machine** (`pages/`, `PhaseRail`, `useSession`): phase transitions
   (Setup → Prepare → Grayscale → WhiteBalance → Gamma → Report), back/refresh
   mid-flow, resuming a session, double-submits.
3. **Loading / empty / error states**: every card must handle `null`, empty,
   loading, and error explicitly (per the Card recipe). Look for cards that
   crash or render stale data when the API returns `null`/error.
4. **Charts** (`charts/`): CIE scatter, gamma, dE overlays with missing points,
   single-point datasets, out-of-gamut values, NaN — no silent mis-scaling.
5. **API client** (`api/client.js`): param encoding, error propagation, and
   wrappers matching the backend contract exactly.
6. **E2E happy path + interruption** (Playwright): a full mocked calibration run
   completes, and an interrupted/retried run recovers correctly.

---

## Output

Two ranked tables (Backend, Frontend), Critical-first. End with a
**"Top 5 fixes for a fast, error-free run"** shortlist. Where a finding is
already covered by an existing test, say so; where coverage is missing, name the
exact new test file and case.

---

> **Related protocols.** `AGENTS.md` defines an `audit QE` protocol focused on
> *test infrastructure* (CI, hardware mocking, fixtures). This document is the
> complementary **bug-hunting** pass — it finds defects rather than designing the
> harness. For a tighter, single-PR diff review, prefer the `/code-review` skill.
