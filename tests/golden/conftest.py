"""
Golden dataset fixtures for regression testing.

The golden dataset is a set of reference CSV measurements with known-good
analysis results.  Tests verify that analyze() output stays within tolerance
bounds, preventing LLM prompt drift or algorithm regressions from silently
changing calibration behaviour.

Updating the golden baseline:
    pytest tests/golden/ --update-golden

This writes new .json baseline files to tests/golden/data/.
Only update intentionally after reviewing the diff.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

GOLDEN_DATA_DIR = Path(__file__).parent / "data"
GOLDEN_TOLERANCE = {
    "grayscale_avg_de": 0.30,
    "grayscale_max_de": 0.50,
    "gamma_midtones": 0.05,
    "pq_err_midtones": 3.0,
    "color_75_avg_de": 0.50,
    "color_100_avg_de": 0.50,
}


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Regenerate golden baseline files instead of asserting against them.",
    )


@pytest.fixture
def update_golden(request) -> bool:
    return request.config.getoption("--update-golden")


@pytest.fixture
def golden_baseline():
    """Load a golden baseline JSON file by name.

    Usage:
        def test_sdr(golden_baseline):
            baseline = golden_baseline("sdr_bt709_grayscale")
            assert summary.grayscale_avg_de == pytest.approx(baseline["grayscale_avg_de"], abs=0.30)
    """
    def _load(name: str) -> Optional[Dict[str, Any]]:
        path = GOLDEN_DATA_DIR / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    return _load


@pytest.fixture
def save_golden():
    """Write a golden baseline JSON file by name."""
    def _save(name: str, data: Dict[str, Any]) -> None:
        GOLDEN_DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = GOLDEN_DATA_DIR / f"{name}.json"
        path.write_text(json.dumps(data, indent=2, default=str))
        print(f"\nGolden baseline updated: {path}")

    return _save


def assert_within_golden_tolerance(
    summary_dict: Dict[str, Any],
    baseline: Dict[str, Any],
    tolerances: Dict[str, float] = GOLDEN_TOLERANCE,
) -> None:
    """Assert that each metric in summary_dict is within tolerance of baseline."""
    failures = []
    for metric, tol in tolerances.items():
        actual = summary_dict.get(metric)
        expected = baseline.get(metric)
        if actual is None or expected is None:
            continue
        delta = abs(actual - expected)
        if delta > tol:
            failures.append(
                f"  {metric}: actual={actual:.4f}, expected={expected:.4f}, "
                f"delta={delta:.4f} > tolerance={tol}"
            )
    if failures:
        raise AssertionError("Golden regression failures:\n" + "\n".join(failures))
