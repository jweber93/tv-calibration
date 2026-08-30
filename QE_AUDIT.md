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
- **Graph evidence is a lead, not proof.** A `trace_path` or `search_graph`
  hit that you have not confirmed by reading the exact source lines is
  **Needs Evidence**, not a confirmed finding. See *Tools* below.
- **Stop after 20 confirmed findings, or when no additional High-confidence
  defects remain** — whichever comes first. Do not pad the report.
- Rank findings by impact on a real calibration run. A wrong dE2000 or an
  inverted slider correction is worse than a cosmetic UI glitch.

## Tools — codebase-memory graph (codebase-memory skill)

This repo is indexed as project `opt-data-tv-calibration` (5300+ nodes /
28k edges, Python + JS/JSX). Use the graph instead of repo-wide grep; reserve
grep for the source confirmation each finding still requires.

**Preflight (every run).**
1. `list_projects` / `index_status` — confirm the project is `ready` and check
   the coverage report. Currently `static/assets/index.css` is flagged
   `parse_partial`; treat graph results there as unreliable.
2. Before relying on graph output for any file, call `check_index_coverage`
   with that path. `indexed_no_recorded_gap` is best-effort, not a
   completeness guarantee.

**Phase 1 (map architecture).** `get_architecture` with
`aspects:["structure","routes","hotspots","clusters"]`. Clusters run Leiden
community detection over the call graph — the de-facto modules — which is how
you detect a `calcore` module importing hardware/web code (package-boundary
invariant, AGENTS.md).

**Phase 2 (entry points).** `search_graph(label="Route", ...)` and
`query_graph` with Cypher `HTTP_CALLS`/`DATA_FLOWS` patterns to enumerate
routes and the CSV/ZRO → analysis → correction math → API → UI data path.
Gotchas: `search_graph(relationship="HTTP_CALLS")` filters by node *degree* —
go through `query_graph` for actual edge lists; `query_graph` has a 100k-row
ceiling — always `LIMIT`.

**Phase 3 (reachability oracle).** For every candidate defect,
`trace_path(function_name=…, direction="both", depth=3)` from the suspect
function. If the inbound direction dead-ends at no live entry point, or the
path only exists via heuristic (not `lsp`) resolvers — downgrade to **Needs
Evidence**. `include_evidence=true` shows the resolver per hop; use it when
the call chain decides a finding.

**Incremental mode.** When a `git diff` is the scope (post-merge sweeps,
mid-branch reviews), start with `detect_changes()` to map the diff to its
blast radius and probe only the affected symbols. Full-repo sweeps are the
exception, not the default.

**Freshness rule.** The index goes stale as the repo changes. After a
rebase/checkout, re-check `index_status` before trusting a trace, and always
read the cited lines before writing the finding. Treat a graph hop the way
you treat "this could cause…" speculation — neither is evidence until
confirmed from source (see issue #268: stale diagnoses survive refactors).

---

## Phased workflow — do not skip ahead

**Phase 1 — Map architecture.** Build an internal model of the modules, the
calibration signal chain, and how state flows (use `get_architecture`; see
Tools). **Report no defects in this phase.**

**Phase 2 — Identify executable calibration code paths.** Enumerate the real
entry points and the paths data takes through them (CSV/ZRO import → analysis
→ correction math → API → UI). Do not assume the phase list below is
complete — enumerate routes via the graph (`label="Route"`), not by memory.

**Phase 3 — Trace each path against the invariants below.** For every candidate
defect, walk the execution from entry point to the defect and confirm it is
reachable with concrete inputs/state (trace + source read, per Tools). A
finding that cannot be traced here does not get reported.

**Phase 4 — Produce findings.** Report **only** defects confirmed in Phase 3.

---

## Ground truth — invariants

The canonical list is AGENTS.md **§ Calibration Invariants**; treat that
section as authoritative and do not restate it here. Audit-specific checks:

- Target **dE2000 < 2.0**; white point **D65 (6504K)** — but grayscale targets
  use the **session** white point, not a hardcoded D65 (regression: #485).
- **SDR** = BT.1886 EOTF / gamma 2.2; **HDR10** = PQ EOTF. EOTF and gamma are
  **not interchangeable** — confirm neither is silently substituted for the
  other (regression: #487, #388). PQ targets are **absolute nits**, hard-
  clipped at measured peak — never scaled to peak (regression: #561/#569/#570).
- Conservative Round-1 correction factor (~0.55) for inter-node bleed.
- Do **not** "correct" the 40–70% firmware tone-mapping wall via menu
  controls (issue #548).
- `CalibrationTarget.primaries` must always be normalized to dict form (#362).
- Code→stimulus mapping flows through `calcore/models.py:_normalize_code` and
  `AnalysisConfig.code_max` (255 / 1023; limited range 16–235 / 64–940);
  sat-bucket thresholds derive from `code_max`, never hardcoded
  (regressions: #547/#564/#572).
- `analyze()` is deterministic: no RNG, dict/set-iteration, or wall-clock
  dependence in the analysis path.

---

## Backend track (Python / FastAPI)

Probe these hotspots:

1. **Color math** (`calcore/`, color-science modules): dE2000 near black,
   degenerate EOTF/BT.1886 inputs, division-by-zero / NaN at CV=0, xyY↔XYZ
   round-trips, gamma vs EOTF mixups. Verify against `tests/golden/data/`
   baselines (`hdr_pq_p3d65_grayscale.json`, `sdr_bt709_grayscale.json`;
   tolerances in `tests/golden/conftest.py:GOLDEN_TOLERANCE`) and
   `tests/test_colour_*`, `tests/test_calcore/test_ciede2000_*`.
2. **Numeric stability** (calibration software lives or dies on this): float
   equality / epsilon misuse, clipping (clamp ranges, off-by-one at
   boundaries), integer/float overflow, NaN/Inf propagation through
   transforms, accumulated rounding across multi-step pipelines, singular /
   ill-conditioned matrix handling, tolerance comparisons.
3. **CSV / ZRO import** (`calibrator/zro_import.py`, `csv_adapter.py`,
   `calcore/csv_import.py`): headerless XYZ-vs-xyY ambiguity, short/truncated
   rows, subsecond timestamps, multiple grayscale passes, dedup/merge
   correctness. Off-by-one in pass boundaries silently corrupts a run.
4. **FastAPI concurrency & lifecycle** (`server.py` — 3.4k+ lines, navigate
   it via the route graph rather than top-to-bottom reading — `session.py`):
   mutable default arguments, cached **mutable** state shared across requests
   (sessions returned by reference and mutated concurrently), async/sync
   misuse (blocking calls in async handlers), thread safety of the session
   store, background-task leaks, concurrent requests on the same session id,
   stale / missing session.
5. **Hardware I/O resilience** (`adb_control.py`, `file_watcher.py`, `autocal*`,
   ZRO bridge): blocking ArgyllCMS calls, USB timeout retry logic, ADB
   command **injection** safety, file-watcher races. A hang here stalls the
   whole loop.
6. **LLM query path** (`calcore/llm.py`): per the recipe, `query_*` functions
   must **never raise** — malformed/partial JSON, HTTP errors, empty
   measurements, and not-configured (`endpoint`/`model` empty → `None`) must
   all degrade gracefully, never 500.
7. **API contracts**: every endpoint's success and error shape; 400 on bad
   input (budget/param validation), not 500; typed null (never 500) on
   unconfigured paths.

## Frontend track (React / Vite)

1. **Numeric & unit correctness** (`utils/fmt.js`, charts, `MeasurementTable`):
   does the UI display the sign-inverted slider values the way an operator
   must *enter* them on the TV? A correct backend value shown with a flipped
   sign is a Critical UX bug.
2. **State machine** (`pages/`, `PhaseRail`, `hooks/useSession`): the nine-step
   session flow is Select Mode → Prepare → Pre-Grayscale → Luminance → White
   Balance → Gamma → Color Tuner → Post-Grayscale → Report (AGENTS.md; pages
   under `frontend/src/pages/` including `SuggestedPatches.jsx` and
   `ComparisonPage.jsx`). Probe phase transitions, back/refresh mid-flow,
   resuming a session, double-submits.
3. **Loading / empty / error states**: every card must handle `null`, empty,
   loading, and error explicitly (per the Card recipe). Look for cards that
   crash or render stale data when the API returns `null`/error.
4. **Charts** (`charts/`): CIE scatter, gamma, dE overlays with missing
   points, single-point datasets, out-of-gamut values, NaN — no silent
   mis-scaling.
5. **API client** (`api/client.js`): param encoding, error propagation, and
   wrappers matching the backend contract exactly.
6. **E2E happy path + interruption** (Playwright, `frontend/tests/`): a full
   mocked calibration run completes, and an interrupted/retried run recovers
   correctly.

> Caveat: JS/JSX is indexed but trace-coverage over it is unverified. A
> frontend finding traced only in the graph needs an exact source read before
> it is **confirmed**; otherwise **Needs Evidence**.

---

## Output format

Two tables (Backend, then Frontend), sorted **Critical-first, then by
Confidence**. Use exactly these columns:

| Severity | Confidence | File:Line | Title | Failure Scenario | Evidence | Root Cause | Fix | Model Tier | Test |
| -------- | ---------- | --------- | ----- | ---------------- | -------- | ---------- | --- | ---------- | ---- |

Column definitions:

- **Severity** — Critical / High / Medium / Low (impact on a real calibration run).
- **Confidence** — **High** = I traced the execution path (graph trace *and*
  source read, per Tools); **Medium** = strong evidence, one assumption
  unverified; **Low** = appears wrong but I could not prove it (consider
  **Needs Evidence** instead of reporting).
- **Failure Scenario** — concrete inputs/state → wrong output or crash.
- **Evidence** — the relevant code path: show how execution **reaches** the
  defect (e.g. "`analyze_session()` hands `primaries=None` to
  `normalize_target()`, which dereferences `primaries` — confirmed by
  `trace_path direction=inbound` and reading `server.py:212`"). Cite
  function names, not bare line numbers — line anchors don't survive rebase
  and the graph tools key off names. Not "this could cause…".
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
  test file and case. Hardware-adjacent tests use the `tests/conftest.py`
  fixtures (`adb_device_found`/`adb_no_device`/`adb_silent`,
  `zro_bridge_up`/`zro_bridge_down`) and the `hardware`/`flaky` markers —
  never a live device in CI.

End with a **"Top 5 fixes for a fast, error-free run"** shortlist. If any
candidate defects were downgraded to **Needs Evidence**, list them separately
below the tables with the specific evidence that would confirm or refute them.

## Filing findings

This is a **bug-hunting pass, not an implementation pass** — do not fix
anything and do not open a PR. Instead, file each confirmed (non–Needs
Evidence) finding as its own GitHub issue:

- **Search first.** Search for an existing open issue covering the same
  defect before filing (GitHub MCP `search_issues`/`list_issues` if
  available; otherwise `curl` against api.github.com with the session token,
  or `gh issue list`). Do not create duplicates, and do not silently skip
  dedupe when no GitHub tool exists — say so in the report.
- **Title**: concise defect summary, e.g. `Grayscale target uses hardcoded
  D65 instead of session white point`.
- **Body**: reuse the row's Failure Scenario, Evidence, Root Cause, Fix, and
  Test columns as sections; include File:Line.
- **Labels**: `bug`, plus severity (`critical`/`high`/`medium`/`low`) and
  track (`backend`/`frontend`) as available in the repo's label set; add
  `math-error` or `hardware-io` where the defect is color math or a
  hardware-comm issue (aligns with the `tv-calibration-bug-hunt` skill).
- One issue per finding — do not batch multiple defects into one issue.
- If a candidate was downgraded to **Needs Evidence**, do not file an issue
  for it; leave it in the report only.

---

> **Related protocols.** `AGENTS.md` defines an `audit QE` protocol focused on
> *test infrastructure* (CI, hardware mocking, fixtures). This document is the
> complementary **bug-hunting** pass — it finds defects rather than designing
> the harness. It is triggered by both "audit QE" (AGENTS.md) and
> "bug hunt tv-calibration" (the `tv-calibration-bug-hunt` skill); treat this
> file as the canonical procedure and the skill as domain shorthand. For a
> tighter, single-PR diff review, prefer the `requesting-code-review` skill.
