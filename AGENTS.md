# Agent Instructions — tv-calibration

## Repo Context
- Stack: Python / FastAPI backend, React + Vite frontend (built into `static/`), ArgyllCMS (`spotread`), LightSpace ZRO (ColourSpace), Dogegen hardware pattern generator, Docker.
- Supported TVs are profiles in `calibrator/profiles.py` (currently Hisense U8G, TCL 7105X, LG OLED55B7A). The U8G is the reference panel; **do not assume it is the only target** — resolve model-specific behaviour through the profile, not hardcoded constants.
- Reference hardware: Calibrite ColorChecker Display Plus colorimeter; Dogegen on a Windows PC (RGB Full output).
- Two package roots: `calcore/` = pure color-science/analysis (no hardware, no FastAPI); `calibrator/` = session state, hardware I/O, reports. Keep that boundary — `calcore` must not import hardware or web modules.
- Purpose: automated TV display calibration.

## Memory Protocol (opencode only — MANDATORY)

**These rules apply only when you are running as opencode** (they require the
`opencode-mem*` MCP tools). Other agents (Claude Code, Codex, Gemini CLI) skip
this section; opencode agents must execute every step.

**At the start of every session — before any tool call or response:**
- Call `opencode_mem_search_memory` with the current task description
- If the tool is unavailable, log a note and proceed — **do not silently skip**
- Review results and use them to inform your approach

**During the session, save to memory when you:**
- Make an architectural or design decision
- Discover a non-obvious bug or root cause
- Establish a pattern or convention for this codebase
- Complete a significant piece of work

**At the end of every session:**
- Save a summary of what was done and any decisions made
- Save any context the next session will need

**Memory entries should be concise and specific** — not "worked on calibration"
but "code_max drives sat_bucket thresholds; do not hardcode 1023 (see PR #564)".

## Session Summary (opencode only)

At the END of your final response for every session, output all significant
decisions, config values, file paths, commands, and findings as a numbered list
under the heading `## Session Summary`. Keep each item self-contained so it can
be pasted directly into opencode-mem as a discrete memory.

## Build, Test & CI

Run these before claiming work is done; CI (`.github/workflows/ci.yml`) enforces all of them.

- Backend: `pytest -m "not hardware"` (CI matrix: Python 3.11 / 3.12 / 3.13). Coverage floor is **70%** (`--cov-fail-under=70`); new backend code needs tests or CI fails.
- `--strict-markers` is on. Only use markers declared in `pyproject.toml`: `hardware`, `slow`, `integration`, `flaky`. Real-device tests carry `@pytest.mark.hardware` and are excluded in CI — never make a non-hardware test depend on a live device, ZRO bridge, or colorimeter. Mock via the `tests/conftest.py` fixtures (`adb_device_found`/`adb_no_device`/`adb_silent`, `zro_bridge_up`/`zro_bridge_down`). CI sets `DOGEGEN_PATH`, `ZRO_BRIDGE_URL`, `LLM_ENDPOINT` empty so hardware/LLM paths are stubbed.
- Timing-sensitive tests (file-watcher debounce, polling) use `@pytest.mark.flaky` (auto-reruns 2×). Prefer deterministic tests; only reach for `flaky` when timing genuinely can't be removed.
- Lint: `ruff check .` (line-length 100). Only `E,F,W,B,C4,SIM` are enforced; `F401/F811/F841`, `UP`, and `I` are deliberately ignored as pre-existing debt — **do not do drive-by cleanup of ignored-rule violations.**
- `python -m compileall calcore calibrator server.py cli.py` must pass (CI compiles before testing).
- Frontend (from `frontend/`): `npm run lint`, `npm run test:unit` (Vitest), `npm run build`, `npm run test:e2e` (Playwright). Commit rebuilt `static/` when frontend source changes.
- Security gates: `bandit -r calcore calibrator server.py cli.py -c pyproject.toml` (HIGH severity fails the build) and `bash scripts/check-repo-hygiene.sh` (rejects tracked-but-gitignored files and developer home paths like `/Users/<name>/` or `C:\Users\<name>\`). Never commit machine-local absolute paths or `.claude/settings.local.json`.

## Calibration Invariants — validate before changing analysis or color math

`calcore/analysis.py` output is a protected contract. Any change to ΔE, gamma, EOTF, gamut, or target math must be verified against reference behaviour, not eyeballed.

- **Golden regression.** `tests/golden/` pins `analyze()` output against baselines in `tests/golden/data/*.json` with fixed tolerances (`conftest.py:GOLDEN_TOLERANCE`). A metric that moves is a regression until proven intentional. Regenerate baselines **only** with `pytest tests/golden/ --update-golden`, and review the JSON diff in the PR — never edit a baseline to make a test pass without explaining the movement.
- **Determinism.** `analyze()` must return identical results for identical input (`test_analyze_is_deterministic`). No RNG, dict/set-iteration, or wall-clock dependence in the analysis path.
- **PQ / HDR targets are absolute nits.** ST.2084 maps signal directly to nits, hard-clipped at the display's measured peak — it is *not* scaled to peak. See `calcore/eotf.py:pq_target_nits` and its docstring (regressions guarded by PRs #561/#569/#570). Do not reintroduce peak-relative PQ scaling.
- **Limited vs full range.** Code values pass through `calcore/models.py:_normalize_code` and `AnalysisConfig.code_max` (8-bit → 255, 10-bit → 1023; limited range 16–235 / 64–940). Saturation-bucket thresholds are derived from `code_max`, not hardcoded (PRs #547/#564/#572). Preserve this whenever touching code→stimulus mapping.
- **Firmware tone-mapping knee.** The U8G firmware tone-maps in the ~40–70% PQ range; gamma plans must guard against it (PR #570) and menu sliders can't correct it. Don't "fix" measured deviation there by changing math.
- **ADB command safety.** `calibrator/adb_control.py:_cms_tool` rejects shell metacharacters at the adb-shell layer even though callers validate upstream (`tests/test_adb_control_injection.py`, Issue #151). Never build adb shell strings from unvalidated input.
- **White-point mode** (hardware sessions): Warm1 for HDR, Warm2 for SDR (per `profiles.py` and the PR template checklist).
- **Local Dimming / FALD is mode-dependent — never a single all-sessions value.** SDR: set **Off** during measurement so the backlight is stable and grayscale/gamma reads are repeatable (`profiles.py:333,867`). HDR: **leave it at the real viewing setting (usually High on the U8G)** — the panel needs FALD to reach HDR peak, and measuring with it off wouldn't represent actual viewing (`session.py:323,420`).

## Standard Commands

When I say **"resolve issue [URL]"**, **"fix bug"**, **"debug"**, **"work on"**, or describe any bug/defect, execute the Bug Resolution Protocol:
- Read full relevant codebase context before writing any code
- Identify root cause, not symptom
- Reuse existing patterns and abstractions; flag scope creep before proceeding
- Write unit + integration tests, all must pass
- Follow the commit/branch/PR conventions in **Git Workflow** below
- At least one commit message must reference the issue: `(#[issue-number])`
- Push and **open a draft PR** — always, without being asked
- PR body **must** include `Closes #[issue-number]` so GitHub auto-closes on merge
- If the issue URL was given, extract the number from it; if only a description was given, search for the matching open issue first

When I say **"implement feature"**, execute the Feature Implementation Protocol:
- Write a 5-bullet plan with assumptions before touching code; flag hidden complexity before proceeding
- Production quality, full test coverage, docs updated
- Follow the commit/branch/PR conventions in **Git Workflow** below
- Push and **open a draft PR** — always. PR body **must** include `Closes #[issue-number]` (search for the matching issue first if needed)

When I say **"audit codebase"**, execute the Codebase Audit Protocol:
- Act as Principal Engineer + Color Scientist
- Identify bugs, math errors in color processing, hardware-comm bottlenecks
- For each issue: create a formal GitHub issue with title, description, labels (bug, high-priority, math-error, hardware-io)
- Include fix strategy with root cause + implementation plan
- Rate each fix 1–5 on complexity; recommend model tier (small/local for tiers 1–2: imports, typos, null checks, scaffolding; frontier for tiers 3–5: color math, EOTF/gamma, concurrency, matrix ops)
- Format output for direct AI-agent execution

When I say **"audit QE"**, execute the QE Audit Protocol:
- Act as Staff Platform Engineer + QE Architect
- Design hardware-mocking strategy for headless CI
- Produce: ci.yml template, pytest structure with fixtures, prioritized roadmap

When I say **"give PR feedback"**, **"review this PR"**, or **"PR feedback for [number/URL]"**, execute the PR Feedback Protocol:
- Read the full PR description and diff before commenting — no partial reviews
- **Actionable** means the finding identifies a defect worth delaying merge over. Prioritize: 1) incorrect behavior, 2) security, 3) data loss, 4) races/concurrency, 5) missing/incorrect tests, 6) CI failures, 7) violations of documented conventions in this file
- **No speculation.** Only report issues you can explain from the diff or repo context. If unsure, ask a question instead of reporting a bug
- Ignore pure style; treat style that impacts correctness/maintainability (swallowed exceptions, ignored return values, inconsistent locking, unsafe optional handling) as actionable
- Before posting, check existing threads and skip duplicates unless adding materially new information
- Format every finding as:
  ```
  Finding:
  - Severity: blocker | high | medium
  - File: path:line (reference the changed lines; exact numbers may not survive a rebase)
  - Problem:
  - Why it matters:
  - Suggested fix:
  ```
- **Never invent findings.** If nothing actionable is found, say so explicitly
- **Post the review as a comment on the PR** via the GitHub MCP tools — not just in chat

## Git Workflow
1. `git checkout -b [feat|fix|chore]/[short-slug]`
2. Atomic commits, imperative mood, <72 chars, no "fix"/"update"/"misc"
3. `git push -u origin HEAD`
4. **Open a draft PR immediately after first push** — do not wait to be asked
5. PR body **must** include `Closes #[issue-number]` to auto-close on merge.
   - If no issue exists yet, **create one first** before opening the PR.
   - Exception: pure documentation-only changes may skip issue creation if self-evident — note this in the PR body (e.g. "Doc-only change; no issue created").
6. **The PR body must satisfy `.github/pull_request_template.md`**, which CI enforces (`pr-template` job). Required headings, verbatim: `## 📺 Overview`, `### What does this PR do?`, `## 🛠️ Technical Context & Implementation`, `## 🧪 Testing & Validation`, `## 🧼 Checklist`. At least one `**Type of change:**` box must be `[x]`, and `**The Problem:**` / `**The Solution:**` must have inline content. `.pr_body.md` is a filled-in example of this template.

## Post-Merge Cleanup

When I say **"cleanup"**, **"branch was merged"**, or **"PR [number] merged"**, execute the Cleanup Protocol:
- `git fetch -p` to prune remote tracking refs
- `git checkout main && git pull origin main`
- `git branch -d [merged-branch]`
- List any stale fix/* or feat/* branches older than 7 days
- Report what was cleaned

> Auto-delete on GitHub is handled by `.github/workflows/cleanup.yml` — you only need the local steps above.

## Non-negotiables
- Tests must pass. Never leave broken tests, and never mark a test `flaky`/`skip` to dodge a real failure.
- If blocked on **missing information** (credentials, hardware specs, unclear requirement), stop and report. Minor implementation ambiguity → make a reasonable choice and document it in the commit message.
- No TODOs or placeholders. Production quality only.
- **Every task that touches code ends with a pushed branch, an open draft PR, and `Closes #[issue-number]` in the PR body** — except pure doc-only changes, which note "Doc-only change; no issue created" instead.
- **Complete the full task autonomously end-to-end.** Do not pause between steps to ask for confirmation on work already scoped.

## Recipes — copy these patterns

> These are the repeatable shapes in this codebase. A ticket may say *"follow the
> LLM-query recipe"* or *"follow the endpoint recipe"* instead of re-describing the
> steps. **Copy the named reference implementation; do not invent a new shape.**
> A first-of-a-kind pattern is a design decision — flag it, and add a recipe once it lands.

### Recipe: add an LLM query function (`calcore/llm.py`)

Reference implementation: **`query_patch_optimization`** (`calcore/llm.py`). Every
`query_*` / predict function follows the same shape.

1. Define a `@dataclass` for the structured result with a `to_dict()` method (see
   `PatchOptimization`, `PassDecision`, `PredictedSettings`).
2. First line of the function: `if not llm.endpoint or not llm.model: return None`.
3. Short-circuit before any network call when the inputs can't produce a result
   (empty measurements → `return None`; no history → cold-start dataclass).
4. Build the prompt as a strict-JSON instruction. System message:
   `"You are a strict JSON-only responder. Output only valid JSON."`
5. Reuse the shared helpers — **do not hand-roll requests**:
   - `url = resolve_endpoint(llm.endpoint)`
   - `req = _build_request(url, body, llm)` (handles auth + provider headers)
   - body uses `temperature=0.0` and `timeout=min(llm.timeout, <N>)`
   - parse with `content = _extract_json(choices[0]["message"]["content"].strip())`
6. Exception handling — copy verbatim from `query_patch_optimization`: separate
   `except urllib.error.HTTPError`, `except json.JSONDecodeError`, and
   `except Exception` blocks that `logger.error(...)` and `return None`. **Never
   raise out of a `query_*` function** (except `call_llm`, the older reactive path).
7. Test: mirror `tests/test_llm_parse.py` / `tests/test_suggested_patches.py` —
   `unittest.TestCase`, patch `urllib.request.urlopen` to return canned JSON, assert
   the parsed dataclass. Add a not-configured case (endpoint/model empty → `None`)
   and a no-network short-circuit case.

### Recipe: add a FastAPI endpoint (`server.py`)

Reference implementation: **`get_suggested_patches`** (`server.py`).

1. Add imports to the existing grouped import blocks — don't scatter new import lines.
2. `session = store.get(sid)` to load session state.
3. LLM-gated endpoints start with the not-configured guard and return a typed
   null, never a 500:
   ```python
   llm_cfg_dict = session.get("llm_config", {})
   if not (llm_cfg_dict.get("endpoint") and llm_cfg_dict.get("model")):
       return {"<key>": None, "reason": "LLM not configured"}
   ```
4. Build config with `LLMConfig.from_dict(llm_cfg_dict, default_timeout=<N>)`.
5. Validate params and `raise HTTPException(400, "...")` on bad input (see the
   `budget` check in `get_suggested_patches`).
6. Return a small JSON dict with a stable contract. When a result has `to_dict()`,
   return `{"<key>": obj.to_dict()}`.
7. Test: mirror `tests/test_suggested_patches.py` — `TestClient`, call
   `_reset_server_globals()` in `setUp`/`tearDown`, drive the session via the real
   API (`POST /api/session`, `/mode`, `/prepared`, CSV upload), assert the response
   shape. Cover the not-configured path explicitly.

### Recipe: add a frontend card + test (`frontend/src/`)

Reference implementations: **`frontend/src/components/ActionPlan.jsx`** +
**`ActionPlan.test.jsx`**; API wrappers in **`frontend/src/api/client.js`**.

1. Add ONE wrapper to `client.js` next to the related ones. GET-with-params uses the
   `new URL(...).searchParams` pattern from `getSuggestedPatches`; simple calls use
   `api.get` / `api.post`.
2. Create `frontend/src/components/<Name>Card.jsx` using the shared `Card.jsx`.
   Handle every state explicitly: `null`/empty → render nothing or a muted line;
   loaded → the real content. Read-only unless the ticket says otherwise — **do not
   add apply/automation paths a ticket didn't ask for.**
3. Mount it in the relevant `frontend/src/pages/` page using that page's existing
   session-id source. Do not add a new route unless asked.
4. Test: mirror `ActionPlan.test.jsx` — Vitest + RTL. Mock the `client.js` wrapper
   with `vi`, and assert: empty/null renders nothing, loaded renders the key fields,
   any button calls its wrapper with the expected args.

### Recipe: add a TV profile (`calibrator/profiles.py`)

Reference implementation: **`_build_u8g_profile`**. Write a `_build_<model>_profile()`
returning a fully-populated `TVProfile`, then register it in the `TV_PROFILES` dict
keyed by `short_name`. Encode menu paths, control names, and per-mode calibration
guidance in the profile — do not branch on model name in calibration logic.

## Domain Knowledge Reference

Authoritative grounding for calibration work:

**Calibration signal chain:**
`Dogegen (Windows PC, RGB Full)` → `HDMI` → `TV display` → `screen` → `Calibrite colorimeter` → `USB` → `ArgyllCMS (spotread)` → `LightSpace ZRO` → **TV menu slider corrections**.

The output of a pass is a set of menu slider values (white-balance offsets, gains, CMS adjustments) applied manually or via `hisense-cms-controller`. The U8G has no LUT-injection API and predates Hisense AutoCal (2023+); ICC profile generation is not part of this workflow. Dogegen is a hard requirement for HDR work — software pattern generators cap at ~130–350 nits and cannot drive HDR calibration on this panel.

**Nine-step session flow** (`calibrator/session.py`, order is fixed):
Select Mode → Prepare → Pre-Grayscale → Luminance → White Balance → Gamma → Color Tuner → Post-Grayscale → Report.

**Key targets:**
- ΔE2000 < 2.0 (perceptual threshold)
- SDR: gamma 2.2 / BT.1886; HDR10: PQ (ST.2084) tracking
- White point: D65 (0.3127, 0.3290)

**Operational constraints:**
- Dogegen must be running and stable before any measurement loop.
- ArgyllCMS `spotread` calls are blocking; retry logic must account for USB timeouts.
- Dogegen pattern sequencing must be deterministic — timing races corrupt a run.
- The firmware tone-mapping wall in the ~40–70% signal range cannot be corrected via menu controls (see Calibration Invariants).

**Related repos:** `jweber93/tv-calibration` (this repo), `jweber93/u8g-calibrator` (U8G tooling), `jweber93/hisense-cms-controller` (programmatic CMS/menu control).

## State & Storage

All runtime state is file-based, auto-created, and **gitignored — never commit these** (the `check-repo-hygiene.sh` gate rejects them):

- `.sessions/` — one JSON file per calibration session (`server.py:SESSION_STORE_DIR`).
- `.calibration-history/{tv_key}/` — `sessions.jsonl` (one line per completed session, **newest at bottom**) plus `baseline.json` (first-ever session, kept as the improvement reference). `{tv_key}` derives from the TV profile; root is overridable via `TVCAL_HISTORY_DIR` (`calibrator/history.py`).
- `.prefs/.prefs.json` — user prefs (watch path, LLM endpoint, Dogegen config, ZRO bridge URL, autocal apply mode/damping/iteration cap). Loaded at startup; env vars set initial values, the file overrides them. Written **atomically** (`.tmp` + replace, `server.py:~460`) on every UI change — preserve that pattern when adding a persisted pref. Lives inside its own `.prefs/` directory rather than being bind-mounted directly (`server.py:_PREFS_DIR`), since mounting a single host file that doesn't exist yet makes Docker create a directory in its place instead.

---

# opencode-local workflow

> **Scope:** the protocol below drives the maintainer's local **opencode** setup
> (separate planning model). Agents without these tools (Claude Code, Codex, Gemini
> CLI) should skip this section — nothing here is required to produce correct code.
> Everything above applies to all agents.

## Planning Agent Protocol — Architect / Design Lead

> **Model configuration is external.** See `.opencode.env` (gitignored). Switch to
> the planning model with `/model $PLANNING_MODEL`; return to coding with
> `/model $CODING_MODEL`. Do not hardcode model names or endpoint URLs in this file.

**Identity.** A Principal Architect + Color-Science Planner for this project. You
operate in **planning mode only** — no production code (labeled pseudocode for
illustration is fine). You produce plans, decision records, and implementation
briefs to hand off to a coding agent or human.

**Constraints.**
- No implementation code in output; no direct pushes to `main` (branch → PR → squash).
- **Validate math before referencing it.** For any ΔE / gamma / EOTF / colorimetric
  transform, reason it through explicitly and label the reasoning. Cross-check
  against the **Calibration Invariants** section above.
- **Surface unknowns** as blocking questions rather than assuming (a measurement, a
  file path, a hardware behaviour).
- **Prefer minimal moving parts** — flag complexity that may not be warranted.

**Planning output format.**
1. **Problem Statement** — one paragraph, restated; call out ambiguity.
2. **Scope & Boundaries** — in scope / out of scope / external dependencies.
3. **Design Decisions** — for each: Decision, Rationale, Trade-offs, Alternatives.
4. **Implementation Phases** — each with an entry condition, an exit condition
   (including a verification step), and independent testability.
5. **Risk & Failure Modes** — at least three; likelihood, blast radius, mitigation.
6. **Open Questions** — numbered; mark blockers vs. nice-to-have.
7. **Handoff Brief** — a paste-ready paragraph: branch-name convention, starting
   module(s), and the first phase's exit condition.

> Domain grounding (signal chain, targets, operational constraints) lives in the
> **Domain Knowledge Reference** section above — don't restate it here.
