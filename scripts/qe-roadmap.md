# QE & GHA Strategy Roadmap

**Project:** tv-calibration  
**Date:** 2026-04-17  
**Audience:** Platform Engineering, QE

---

## Executive Summary

The current pipeline (`tests.yml`) is solid: 70 % coverage gate, multi-Python
matrix, bandit, Playwright E2E.  This roadmap layers in hardware mocking,
ruff linting, golden-dataset regression protection, and a cleaner multi-stage
CI that can gate on lint before spending test minutes.

---

## Quick Wins (< 1 day each)

### QW-1 — Add `ruff` to the CI pipeline
**Why:** bandit covers security; nothing currently checks style/formatting.
Ruff replaces flake8 + isort + pyupgrade in one binary, runs in milliseconds.

```toml
# pyproject.toml additions
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "C4", "SIM"]
ignore = ["E501"]
```

**File:** `.github/workflows/ci.yml` (already includes `lint-python` job using
`astral-sh/ruff-action@v3`).

---

### QW-2 — Adopt `ci.yml` and retire `tests.yml`
**Why:** `ci.yml` adds stage ordering (Lint → Test → Scan), concurrency
cancellation, `golden-regression` job, `version` output, and `trivy` filesystem
scan.  Both files co-exist until `ci.yml` is confirmed stable on a PR.

**Migration steps:**
1. Merge `ci.yml` to main alongside `tests.yml`.
2. Confirm all jobs pass for one week.
3. Delete `tests.yml`; update branch-protection rule to require `merge-gate`
   from `ci.yml`.

---

### QW-3 — Seed the golden baseline files
**Why:** `tests/golden/` tests skip if no `.json` baseline exists.  Run once
to capture the current "good" state, then commit those files.

```bash
pytest tests/golden/ --update-golden
git add tests/golden/data/
git commit -m "chore: seed golden calibration baselines"
```

---

### QW-4 — Exclude `hardware` marker from CI explicitly
**Current state:** `pytest` runs all markers; hardware tests are implicitly
skipped only if subprocess/httpx mocks are wired correctly.

**Fix:** The `ci.yml` backend-tests job uses `pytest -m "not hardware"`.
Confirm `pyproject.toml` marks all ADB/ZRO/colorimeter tests with
`@pytest.mark.hardware`.

---

### QW-5 — Add `actionlint` to protect workflow files
**Why:** Typos in `${{ }}` expressions fail at runtime, not at review time.
The `lint-shell` job in `ci.yml` already includes `rhysd/actionlint@v1.7.7`.

---

## Medium-Term Infrastructure (1–2 weeks each)

### MT-1 — Dogegen subprocess mock fixtures
**Delivered:** `tests/fixtures/hardware_mocks.py` provides:
- `dogegen_running` — Popen mock that stays alive
- `dogegen_crash_on_start` — immediate exit with rc=1
- `dogegen_not_found` — FileNotFoundError on launch
- TV model profile parametrization (`tv_model` fixture)

**Next step:** Wire these into `test_server_api.py` for the
`POST /api/dogegen/start` and `GET /api/dogegen/status` endpoint tests.

---

### MT-2 — CSV watch-folder injection fixture
**Delivered:** `sdr_grayscale_csv`, `hdr_grayscale_csv`, and
`watch_folder_with_csv` fixtures in `hardware_mocks.py`.

**Next step:** Add integration tests that:
1. Start a session via `POST /api/session`
2. Write a CSV to the watch folder via the fixture
3. Call `POST /api/session/{sid}/watch` to point the watcher at `tmp_path`
4. Assert the import event fires and patches are stored correctly

---

### MT-3 — Parametrized TV-model test matrix
**Pattern:** Use `tv_model` fixture from `hardware_mocks.py`:

```python
@pytest.mark.integration
def test_analyze_all_tv_models(tv_model, sdr_grayscale_csv, hdr_grayscale_csv):
    csv = hdr_grayscale_csv if tv_model["mode"] == "hdr" else sdr_grayscale_csv
    patches = parse_measurement_csv(csv.read_text())
    cfg = AnalysisConfig(
        mode=tv_model["mode"],
        eotf=tv_model["eotf"],
        target_space=tv_model["target_space"],
        code_max=tv_model["code_max"],
    )
    summary = analyze(patches, cfg)
    assert summary.grayscale_avg_de is not None
    assert summary.measured_patch_count >= 5
```

This catches regressions in `analyze()` that only surface on specific
code_max/eotf combinations (e.g., SDR 8-bit 0–235 vs HDR 10-bit 0–1023).

---

### MT-4 — Dependabot for Python and Actions dependencies
**Why:** `requirements.txt` pins `>=` floors but not ceilings.  Dependabot
creates weekly PRs for patch/minor bumps, keeping the dep graph auditable.

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: pip
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 5

  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
```

No Docker images to track yet (no Dockerfile); add a `docker` entry when
containerisation lands.

---

### MT-5 — LLM response regression tests
**Why:** `calcore/llm.py` parses free-text LLM responses into structured
adjustment plans.  The parser (`parse_adjustment_plan`) is already tested in
`tests/test_llm_parse.py`, but there are no tests covering prompt drift
(LLM starts recommending implausible values).

**Strategy:**
1. Collect 5–10 representative LLM responses from real calibration sessions.
2. Store as fixtures in `tests/golden/data/llm_responses/`.
3. Write parametrized tests that call `parse_adjustment_plan` on each and
   verify the extracted numeric adjustments are within sane bounds:
   - White balance gain: –50 to +50
   - CMS hue: –10 to +10 degrees
   - Gamma: 2.0 to 2.6 for SDR

---

## Long-Term Infrastructure (1–4 weeks each)

### LT-1 — Docker containerisation
**Why:** Reproducible deployments; enables Trivy image scanning.

**Proposed Dockerfile:**
```dockerfile
FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS prod
COPY . .
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
```

**CI additions in `ci.yml`:**
- `build-docker` job: `docker build --tag $IMAGE_NAME:$TAG .`
- `security-trivy` job: switch scan-type from `fs` to `image`
- On `refs/tags/v*`: push to GHCR with `packages: write` permission

---

### LT-2 — GitHub Advanced Security (SARIF) for bandit
**Current:** bandit outputs plain text.  SARIF upload requires GitHub Advanced
Security (GHAS), which is free for public repos and paid for private.

**If GHAS is available:**
```yaml
- run: bandit -r calcore calibrator server.py cli.py -c pyproject.toml -f sarif -o bandit.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: bandit.sarif
```

This surfaces findings as inline PR annotations.

---

### LT-3 — Property-based testing with Hypothesis
**Why:** Handcrafted CSV fixtures cover happy paths.  Hypothesis generates
edge cases automatically (near-black patches, NaN luminance, single-patch
imports, identical R=G=B=0 entries).

**Target:** `calcore/analysis.py` and `calcore/colour.py`.

```python
from hypothesis import given, strategies as st
from calcore.colour import ciede2000, xyz_to_lab

@given(
    X=st.floats(0.0, 100.0, allow_nan=False),
    Y=st.floats(0.0, 100.0, allow_nan=False),
    Z=st.floats(0.0, 100.0, allow_nan=False),
)
def test_ciede2000_identical_points_is_zero(X, Y, Z):
    lab = xyz_to_lab((X, Y, Z))
    assert ciede2000(lab, lab) == pytest.approx(0.0, abs=1e-9)
```

---

### LT-4 — Coverage ratchet (80 % target)
**Current:** 70 % minimum.  The `calibrator/session.py` state machine (2 040
lines) is the biggest untested surface area.

**Plan:**
1. Add `session.py` state-machine scenario tests (phase transitions, quality
   gate failures, multi-pass grayscale sequences).
2. Raise `--cov-fail-under` to 75 % once those tests land, then 80 %.

---

## Priority Matrix

| Item | Effort | Risk-Reduction | Recommended Order |
|------|--------|---------------|-------------------|
| QW-1 ruff | 1h | Medium | 1 |
| QW-2 adopt ci.yml | 2h | High | 2 |
| QW-3 seed golden baselines | 30m | High | 3 |
| QW-4 hardware marker | 1h | Medium | 4 |
| QW-5 actionlint | 30m | Low | 5 |
| MT-1 dogegen mocks | 1d | High | 6 |
| MT-2 watch-folder fixture | 1d | High | 7 |
| MT-3 TV-model matrix | 2d | Medium | 8 |
| MT-4 Dependabot | 1h | Medium | 9 |
| MT-5 LLM regression tests | 3d | High | 10 |
| LT-1 Docker | 1w | Medium | 11 |
| LT-2 SARIF/GHAS | 1d | Low | 12 |
| LT-3 Hypothesis | 2w | Medium | 13 |
| LT-4 Coverage ratchet | 2w | Medium | 14 |

---

## Files Created by This Audit

| File | Purpose |
|------|---------|
| `.github/workflows/ci.yml` | Enhanced multi-stage CI workflow |
| `tests/fixtures/__init__.py` | Package marker |
| `tests/fixtures/hardware_mocks.py` | Dogegen / ArgyllCMS / TV-model fixtures |
| `tests/golden/__init__.py` | Package marker |
| `tests/golden/conftest.py` | Golden baseline load/save fixtures + `--update-golden` flag |
| `tests/golden/test_golden_regression.py` | SDR + HDR regression tests |
| `scripts/qe-roadmap.md` | This document |
