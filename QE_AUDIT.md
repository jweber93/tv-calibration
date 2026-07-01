# QE Audit — tv-calibration

Act as a **Staff QE Architect + Color Scientist**. Your goal is a calibration
process that is **fast, deterministic, and error-free** end to end:
Dogegen → U8G → Calibrite → ArgyllCMS → ZRO → TV menu sliders.

Hunt for **real defects** in both the FastAPI backend and the React frontend.
Do not propose features or refactors.

## Operating principles — read first

- **Assume existing behavior is correct unless you can prove an invariant is
  violated through a concrete code path. Prefer false negatives over false
  positives.**
- **Only report defects you can trace through executable code paths.** If a
  defect depends on behavior you cannot verify from the repository, mark it
  **Needs Evidence** instead of reporting it as a confirmed bug.
- **Do not report possible bugs that lack a concrete execution path.** No
  "this could cause…", "this may happen…", or speculative findings. If you
  cannot show how execution reaches the defect, do not report it.
- **Stop after 20 confirmed findings, or when no additional High-confidence
  defects remain** — whichever comes first. Do not pad the report.
- Rank findings by impact on a real calibration run. A wrong dE2000 or an
  inverted slider correction is worse than a cosmetic UI glitch.

---

## Phased workflow — do not skip ahead

**Phase 1 — Map architecture.** Build an internal model of the modules, the
calibration signal chain, and how state flows. **Report no defects in this
phase.**

**Phase 2 — Identify executable calibration code paths.** Enumerate the real
entry points and the paths data takes through them (CSV/ZRO import → analysis →
correction math → API → UI).

**Phase 3 — Trace each path against the invariants below.** For every candidate
defect, walk the execution from entry point to the defect and confirm it is
reachable with concrete inputs/state. A finding that cannot be traced here does
not get reported.

**Phase 4 — Produce findings.** Report **only** defects confirmed in Phase 3.

---

## Ground truth — these invariants MUST hold

- Target **dE2000 < 2.0**; white point **D65 (6504K)** — but grayscale targets
  use the **session** white point, not a hardcoded D65 (regression: #485).
- **SDR** = BT.1886 EOTF / gamma 2.2; **HDR10** = PQ EOTF. EOTF and gamma are
  **not interchangeable** — confirm neither is silently substituted for the
  other (regression: #487, #388).
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
2. **Numeric stability** (calibration software lives or dies on this): float
   equality / epsilon misuse, clipping (clamp ranges, off-by-one at boundaries),
   integer/float overflow, NaN/Inf propagation through transforms, accumulated
   rounding across multi-step pipelines, singular / ill-conditioned matrix
   handling, tolerance comparisons.
3. **CSV / ZRO import** (`calibrator/zro_import.py`, `csv_adapter.py`):
   headerless XYZ-vs-xyY ambiguity, short/truncated rows, subsecond timestamps,
   multiple grayscale passes, dedup/merge correctness. Off-by-one in pass
   boundaries silently corrupts a run.
4. **FastAPI concurrency & lifecycle** (`server.py`, `session.py`): mutable
   default arguments, cached **mutable** state shared across requests
   (sessions returned by reference and mutated concurrently), async/sync misuse
   (blocking calls in async handlers), thread safety of the session store,
   background-task leaks, concurrent requests on the same session id, stale /
   missing session.
5. **Hardware I/O resilience** (`adb_control.py`, `file_watcher.py`, ZRO
   bridge): blocking ArgyllCMS calls, USB timeout retry logic, ADB command
   **injection** safety, file-watcher races. A hang here stalls the whole loop.
6. **LLM query path** (`calcore/llm.py`): per the recipe, `query_*` functions
   must **never raise** — malformed/partial JSON, HTTP errors, empty
   measurements, and not-configured (`endpoint`/`model` empty → `None`) must all
   degrade gracefully, never 500.
7. **API contracts**: every endpoint's success and error shape; 400 on bad
   input (budget/param validation), not 500; typed null (never 500) on
   unconfigured paths.

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

## Output format

Two tables (Backend, then Frontend), sorted **Critical-first, then by
Confidence**. Use exactly these columns:

| Severity | Confidence | File:Line | Title | Failure Scenario | Evidence | Root Cause | Fix | Model Tier | Test |
| -------- | ---------- | --------- | ----- | ---------------- | -------- | ---------- | --- | ---------- | ---- |

Column definitions:

- **Severity** — Critical / High / Medium / Low (impact on a real calibration run).
- **Confidence** — **High** = I traced the execution path; **Medium** =
  strong evidence, one assumption unverified; **Low** = appears wrong but I
  could not prove it (consider **Needs Evidence** instead of reporting).
- **Failure Scenario** — concrete inputs/state → wrong output or crash.
- **Evidence** — the relevant code path: show how execution **reaches** the
  defect (e.g. "`server.py:212` passes `None` into `normalize_target()`, which
  dereferences `primaries`"). Not "this could cause…".
- **Root Cause** — the underlying reason, not the symptom (e.g. "the cache
  returns shared mutable session instances" — not "missing null check").
- **Fix** — strategy that addresses the root cause.
- **Model Tier** — rate each fix on a 1–5 complexity scale (1 = trivial, 5 =
  requires deep color-science + multi-module reasoning). Then recommend:
  - **Local / low-cost** (small open models, fast inference) for tiers 1–2:
    missing imports, typo fixes, simple null checks, test scaffolding, formatting.
  - **Frontier / high-cost** (GPT-4, Claude, etc.) for tiers 3–5: color math
    corrections (dE2000, CIE transforms), EOTF/gamma fixes, concurrency bugs,
    matrix operations, multi-step calibration pipeline changes.
  When in doubt, over-rate — a wrong fix on color math is far more expensive
  than an extra $0.02 in API cost.
- **Test** — the regression test that would have caught it. Mirror existing
  patterns: pytest `TestClient` for backend, Vitest+RTL / Playwright for FE.
  If existing coverage already exists, say so; if missing, name the exact new
  test file and case.

End with a **"Top 5 fixes for a fast, error-free run"** shortlist. If any
candidate defects were downgraded to **Needs Evidence**, list them separately
below the tables with the specific evidence that would confirm or refute them.

---

> **Related protocols.** `AGENTS.md` defines an `audit QE` protocol focused on
> *test infrastructure* (CI, hardware mocking, fixtures). This document is the
> complementary **bug-hunting** pass — it finds defects rather than designing the
> harness. For a tighter, single-PR diff review, prefer the `/code-review` skill.
