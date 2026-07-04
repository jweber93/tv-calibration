# QE Audit — Findings (tv-calibration)

Bug-hunting pass per `QE_AUDIT.md`. Phased workflow followed: architecture map →
executable-path enumeration → per-path invariant tracing → findings. Only
defects traceable through a concrete execution path are reported. Baseline: full
backend suite green (`1461 passed`) before auditing, so every finding below is a
genuine coverage/behaviour gap, not a pre-existing red test.

## Scope traced and found correct (no defects)

To keep the report honest (the recipe says *prefer false negatives over false
positives*), these hot paths were traced end-to-end and **confirmed correct** —
they are listed so the empty findings there are understood as "checked", not
"skipped":

- **CIEDE2000** (`calcore/colour.py`) — T-term phase offsets, hue-average /
  hue-difference branches, and RT all match Sharma-Wu-Dalal; matches the
  in-repo reference test.
- **EOTF** (`calcore/eotf.py`) — BT.1886 algebra reduces to the standard
  `((Lw^{1/γ}−Lb^{1/γ})·V + Lb^{1/γ})^γ` (verified L(0)=Lb, L(1)=Lw); PQ ST.2084
  constants correct; `pq_target_nits` clips at panel peak (invariant holds).
- **CCT** McCamy (`calcore/models.py:cct`) — verified numerically (D65 → 6505 K),
  denominator sign is correct despite looking inverted.
- **Autocal correction sign** (`calibrator/autocal.py` + `autocal_loop.py`) —
  `step = −damping·error/gain`; error sign from `cms_hints` matches "too high →
  lower control" for hue/sat/brightness. No inversion.
- **WB recommendation direction** (`calibrator/guidance.py:wb_hints/…candidates`)
  — x-too-high → lower red / raise blue, etc. Directions consistent.
- **Signal-range/code-scale coupling** — `10bit + limited` (which would break the
  8-bit-only `_normalize_code`) is **unreachable**: `set_code_scale` rejects it
  (`session.py:1895`) and `set_signal_range` resets code_scale via
  `recommended_code_scale`. No defect.
- **ADB injection** (`calibrator/adb_control.py`) — every CmsTool arg is
  `re.fullmatch(r"[A-Za-z0-9_\-]+")`-validated; device serials are argv, never
  shell-interpolated.
- **LLM `query_*`** (`calcore/llm.py`) — HTTP errors, malformed/partial JSON,
  empty choices, and not-configured all degrade to `None`; never 500.
- **Round-1 damping factor** — `_ROUND1_DAMPING_FACTOR = 0.55`, applied only on
  round 0 (invariant holds — but see Needs-Evidence NE-1 for a bypass).
- **Store concurrency** — `threading.RLock`, reentrant get→mutate→save is atomic.
- **Frontend** `fmt.js`, `api/client.js`, `GammaChart`, `DeChart`, `CIEScatter`,
  `MeasurementTable`, `Setup`, `useSession` — null/empty/loading handled; no
  sign-flip on displayed slider values (backend sends pre-rendered
  "Raise/Lower … by N" strings, FE does no sign math).

---

## Backend

| Severity | Confidence | File:Line | Title | Failure Scenario | Evidence | Root Cause | Fix | Model Tier | Test |
| -------- | ---------- | --------- | ----- | ---------------- | -------- | ---------- | --- | ---------- | ---- |
| **High** | **High** | `calibrator/reports.py:20-48` | Report ΔE stats average **all accumulated passes**, not the latest pass | User measures a poor pre-cal ramp, re-measures it, and re-imports (two `/import/zro` calls). `pre_measurements` now holds 2 ramps ≥45 s apart. The report's headline `pre_cal.avg_de` = **10.67 / max 23.29**, while every other consumer (live view, quality gate) reports the correct latest-pass **2.48 / max 3.98**. Same dilution hits `post_cal`, so `improvement_pct` is wrong too. | `report_payload` calls `stats(session["pre_measurements"])` / `stats(session["post_measurements"])` (`reports.py:41-42`) which iterates the **raw** bucket. Every other reader first reduces to the latest pass: `session_view` step `pre/post_grayscale` uses `latest_grayscale_pass(...)` (`session.py:1396`); the QG uses it too (`quality.py:142`). `record_session` then persists the diluted numbers into per-TV history (`server.py:1669`), poisoning future LLM context. Reproduced: 10.67 vs 2.48. | `report_payload` is the only measurement consumer that never collapses a bucket to its most-recent pass; it treats the append-only bucket as a single ramp. | In `report_payload`, reduce grayscale buckets with `latest_grayscale_pass(bucket, max_count=len(grayscale_levels_for_ramp(ramp)), signal_range, code_scale)` before `stats()`, mirroring `session_view`. Apply the matching reducers to the other buckets (see next row). | 3 (Frontier — must not silently change the meaning of the headline metric; needs the same pass-detection semantics as the live view, plus history-recording regression coverage). | New `tests/test_delta_report.py::test_report_uses_latest_grayscale_pass`: seed `pre_measurements` with two timestamp-separated ramps (bad then good) and assert `pre_cal.avg_de` equals the good-ramp average, not the blend. Existing single-pass tests still pass (`latest_grayscale_pass` on one pass is identity). |
| **High** | **High** | `calibrator/reports.py:33-45` | Same dilution for gamma / WB / CMS report buckets (same root cause) | Iterative CMS tuning appends every re-measure of Red/Green/… to `cms_measurements`; multi-pass gamma likewise. `stats(cms)` / `gamma_stats(gamma)` average **all** readings (early bad + final good), so `color_tuner.avg_de` and `gamma.avg_gamma` are diluted — while the QG uses latest-per-colour (`quality.py:114-120`) and the gamma view uses `latest_gamma_pass` (`session.py:1496`). | `gamma = gamma_stats(session["gamma_measurements"])` and `cms = stats(session["cms_measurements"])` / `wb = stats(session["wb_measurements"])` (`reports.py:43-45`) all read raw buckets; no latest-pass / latest-per-item reduction. | Same as the row above — `report_payload` skips the per-bucket reduction the rest of the app applies. | Reduce each bucket with its matching helper before `stats()`: `latest_gamma_pass` for gamma, `latest_wb_measurements` for WB, latest-reading-per-colour for CMS. | 3 (Frontier — several bucket types, each with different reduction logic). | Extend the report test with multi-pass CMS + gamma buckets; assert report metrics match the QG's latest-only figures. |
| Low | High | `calibrator/history.py:245,124` | `history_summary.session_count` under-counts once a TV has >10 sessions | A TV with 15 recorded calibrations: `GET /api/session/{sid}/llm/history-summary` returns `session_count: 10`, because `history_summary()` calls `load_history(tv_key)` with the default `limit=10` and reports `len(history)`. | `history_summary` (`history.py:245`) → `history = load_history(tv_key)`; `load_history(..., limit=10)` (`history.py:124`) truncates to 10; `session_count = len(history)` (`history.py:251`) then caps at 10. Exposed via `server.py:1382`. `latest_*` fields are still correct (index 0). | The summary reuses the display-capped loader instead of counting the full JSONL. | Count lines directly (e.g. a dedicated `count_sessions(tv_key)` that reads the file without the display limit), or pass `limit=None`/a large cap for the count. | 1 (Local — trivial). | `tests/test_history_metrics.py`: record 12 sessions, assert `history_summary()["session_count"] == 12`. |

## Frontend

| Severity | Confidence | File:Line | Title | Failure Scenario | Evidence | Root Cause | Fix | Model Tier | Test |
| -------- | ---------- | --------- | ----- | ---------------- | -------- | ---------- | --- | ---------- | ---- |
| — | — | — | **No confirmed defects** in the reviewed FE surface | The audit's FE hotspots (sign-inverted slider display, chart NaN/empty/single-point, API-client contract, null card states) were traced and found correct — see "Scope traced and found correct" above. | `CIEScatter` filters `(0,0)` blacks only (chromaticity is undefined there, correct); `GammaChart`/`DeChart` guard empty; `client.js` POST/GET verbs match the server routes; slider directions are server-rendered strings. | n/a | n/a | n/a | n/a |

---

## Top 5 fixes for a fast, error-free run

1. **`report_payload` latest-pass reduction (grayscale)** — the single
   highest-impact fix: it corrects the headline before/after ΔE and
   `improvement_pct` on the primary deliverable and stops poisoning per-TV
   history. (`reports.py:41-48`)
2. **`report_payload` latest reduction for gamma/WB/CMS** — same root cause;
   finishes the job so no report metric blends stale passes. (`reports.py:33-45`)
3. **`history_summary.session_count` full count** — cheap correctness fix on an
   LLM-context/API field. (`history.py:245`)
4. **(Needs Evidence NE-1) Round-1 damping bypass on null `from`** — worth
   confirming, because the whole point of #575 is a clamp that holds *regardless*
   of model compliance.
5. Add the multi-pass regression tests named above so the report/QG numbers can
   never diverge again — this class of bug is invisible to every current
   single-pass test.

---

## Needs Evidence (downgraded — not reported as confirmed bugs)

- **NE-1 — `_apply_round1_damping` skipped when an adjustment's `from` is null.**
  `calcore/llm.py:1544-1549`: if `from` (or `to`) is `None` the adjustment is
  passed through **undamped**. The prompt schema explicitly permits
  `"from": <current value or null>` (`llm.py:1698`), so a compliant model can
  emit `from:null` and defeat the deterministic Round-1 clamp that #554/#575
  added precisely to hold "regardless of whether the model complied."
  *Evidence that would confirm/refute:* trace how a `from:null` **absolute**
  adjustment is consumed downstream (OSD translate / `next-settings` apply /
  the UI action plan). If it is applied as a full absolute set on round 1, the
  ~0.55 damping invariant is violated for that control → confirmed defect. If
  `from:null` adjustments are dropped or otherwise re-damped downstream, it is a
  non-issue.

- **NE-2 — vestigial `mode` arg in `api.createSession`.**
  `frontend/src/api/client.js:58-59` accepts `mode` but never sends it; the flow
  works because `Setup.jsx` calls `onConfirmMode` separately. No wrong output
  today, so **not** reported as a defect — but it is a latent trap if a future
  caller relies on `createSession` to set the mode. Delete the dead parameter.
