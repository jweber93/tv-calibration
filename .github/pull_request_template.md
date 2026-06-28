## 📺 Overview
<!-- A brief summary of the changes and the problem they solve. -->

Closes #

### What does this PR do?
* **Type of change:** [ ] Bug fix | [ ] New feature | [ ] Refactoring | [ ] CI/CD or Tooling
* **Component(s) affected:** [e.g., ArgyllCMS wrapper, UI/Display output, Python data orchestration]

---

## 🛠️ Technical Context & Implementation
<!-- Think of this as the "architectural diff" for the reviewer. -->

* **The Problem:**
* **The Solution:**
* **Impact on existing workflows/APIs:**

### Mathematical / Data Integrity Checks (If Applicable)
* [ ] Matrix transformations or LUT calculations have been validated.
* [ ] Floating-point precision or data truncation risks have been addressed.

---

## 🧪 Testing & Validation
<!-- How can the reviewer be sure this works? -->

### Environment Details
* **OS / Target Display:** [e.g., macOS / Windows 11 / Hisense U8G]
* **Hardware/Mock Used:** [e.g., Calibrite ColorChecker Display Plus, or Simulated ArgyllCMS output]

### Verification Steps
1. Run `python -m pytest tests/test_calibration_module.py`
2. Run the calibration CLI with the following flags: `--verify --target-gamma=2.2`
3. Check that the resulting delta E (ΔE₂₀₀₀) values fall within acceptable tolerances (< 2.0).

### Firmware / TV Settings
* [ ] Verified against target display firmware version
* [ ] Local Dimming set to Medium during test runs
* [ ] Correct white point mode used (Warm1 for HDR, Warm2 for SDR)

---

## 📸 Visual Evidence (UI / Plot Changes)
<!-- Toggle this section if there are changes to GUI elements or generated color charts/plots. -->
<details>
<summary><b>Click to expand Visuals</b></summary>

### Before
<!-- Insert screenshot, gamut plot, or terminal output log here -->

### After
<!-- Insert screenshot, gamut plot, or terminal output log here -->

</details>

---

## 🧼 Checklist
* [ ] Code adheres to project style guidelines (formatting, typing, linting).
* [ ] Unit tests added/updated and passing.
* [ ] Documentation (README, inline docstrings) updated to reflect changes.
* [ ] No credentials, local absolute paths, or hardware-specific hardcoding leaked.
