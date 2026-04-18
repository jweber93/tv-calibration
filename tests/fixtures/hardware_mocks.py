"""
Hardware mock fixtures for ArgyllCMS / dogegen CLI interfaces.

These fixtures let integration tests run in a headless GitHub Runner without
a physical colorimeter, pattern generator, or Android TV.

Usage in a test file:
    from tests.fixtures.hardware_mocks import dogegen_running, dogegen_not_found

Or register the fixtures project-wide by importing this module in conftest.py:
    pytest_plugins = ["tests.fixtures.hardware_mocks"]

Hardware abstraction layers:
  - dogegen   → subprocess.Popen in server.py
  - ArgyllCMS → CSV files written to watch_folder (mocked via tmp_path)
  - ADB       → subprocess.run (already in conftest.py; extended here)
  - ZRO Bridge → httpx.get / httpx.post (already in conftest.py)
"""
from __future__ import annotations

import subprocess
import textwrap
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest


# ── Dogegen subprocess mocks ──────────────────────────────────────────────────

def _dogegen_popen(returncode: Optional[int] = None, startup_stdout: str = "") -> MagicMock:
    """Build a mock subprocess.Popen for dogegen.

    returncode=None means the process is still running (poll() → None).
    returncode=0 means the process has exited cleanly.
    """
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = returncode
    proc.pid = 12345
    proc.stdout = BytesIO(startup_stdout.encode())
    proc.stderr = BytesIO(b"")
    proc.returncode = returncode

    def _communicate(timeout=None):
        return startup_stdout.encode(), b""

    proc.communicate.side_effect = _communicate
    return proc


@pytest.fixture
def dogegen_running():
    """Patch subprocess.Popen: dogegen starts and stays running."""
    proc = _dogegen_popen(returncode=None, startup_stdout="Dogegen v2.3 ready\n")
    with patch("subprocess.Popen", return_value=proc) as mock_popen:
        yield mock_popen, proc


@pytest.fixture
def dogegen_crash_on_start():
    """Patch subprocess.Popen: dogegen exits immediately with rc=1."""
    proc = _dogegen_popen(returncode=1)
    with patch("subprocess.Popen", return_value=proc) as mock_popen:
        yield mock_popen, proc


@pytest.fixture
def dogegen_not_found(monkeypatch):
    """Simulate DOGEGEN_PATH pointing to a non-existent binary."""
    monkeypatch.setenv("DOGEGEN_PATH", "/nonexistent/Dogegen.exe")

    def _raise(*args, **kwargs):
        raise FileNotFoundError("No such file or directory: '/nonexistent/Dogegen.exe'")

    with patch("subprocess.Popen", side_effect=_raise):
        yield


# ── ArgyllCMS CSV injection fixtures ─────────────────────────────────────────
# The project reads ColourSpace/ZRO CSV exports from a watch folder.
# "Mocking ArgyllCMS" means writing pre-baked CSVs to tmp_path and pointing
# the file-watcher at that directory.

# Minimal 11-point SDR BT.709 grayscale ramp at 100 nit peak.
# Values are calibrated to produce gamma ≈ 2.2 and ΔE2000 < 2.0.
_SDR_GRAYSCALE_CSV = textwrap.dedent("""\
    Date and time\t R\t G\t B\t Y\t x\t y\t msec
    01/01/2025 12:00:00\t16\t16\t16\t0.010\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:03\t26\t26\t26\t0.480\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:06\t51\t51\t51\t2.400\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:09\t77\t77\t77\t6.800\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:12\t102\t102\t102\t14.00\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:15\t128\t128\t128\t23.90\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:18\t153\t153\t153\t37.70\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:21\t179\t179\t179\t55.00\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:24\t204\t204\t204\t74.00\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:27\t230\t230\t230\t90.00\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:30\t235\t235\t235\t100.0\t0.3127\t0.3290\t 360ms
""")

# 11-point HDR10/PQ grayscale ramp at 1000 nit peak (BT.2020 primaries).
_HDR_GRAYSCALE_CSV = textwrap.dedent("""\
    Date and time\t R\t G\t B\t Y\t x\t y\t msec
    01/01/2025 12:00:00\t64\t64\t64\t0.010\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:03\t102\t102\t102\t0.460\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:06\t204\t204\t204\t3.900\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:09\t307\t307\t307\t16.50\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:12\t409\t409\t409\t53.00\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:15\t511\t511\t511\t115.0\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:18\t614\t614\t614\t220.0\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:21\t716\t716\t716\t380.0\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:24\t818\t818\t818\t600.0\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:27\t921\t921\t921\t820.0\t0.3127\t0.3290\t 360ms
    01/01/2025 12:00:30\t1023\t1023\t1023\t1000.0\t0.3127\t0.3290\t 360ms
""")


@pytest.fixture
def sdr_grayscale_csv(tmp_path) -> Path:
    """Write a minimal calibrated SDR grayscale CSV to a temp directory."""
    p = tmp_path / "sdr_grayscale.txt"
    p.write_text(_SDR_GRAYSCALE_CSV)
    return p


@pytest.fixture
def hdr_grayscale_csv(tmp_path) -> Path:
    """Write a minimal calibrated HDR10/PQ grayscale CSV to a temp directory."""
    p = tmp_path / "hdr_grayscale.txt"
    p.write_text(_HDR_GRAYSCALE_CSV)
    return p


@pytest.fixture
def watch_folder_with_csv(tmp_path, sdr_grayscale_csv) -> Path:
    """Return a watch folder (tmp_path) that already contains a ZRO CSV file."""
    return tmp_path


# ── TV-model response fixtures ────────────────────────────────────────────────
# These simulate different TV model profiles and their characteristic responses.
# Use the `tv_model` fixture with indirect parametrization for matrix tests.

TV_MODEL_PROFILES: Dict[str, Dict[str, Any]] = {
    "lg_c3_sdr": {
        "profile_name": "LG C3 OLED",
        "mode": "sdr",
        "eotf": "bt1886",
        "target_space": "bt709",
        "code_max": 235,
        # Typical uncalibrated white point: slightly cool (D65 push)
        "uncal_x": 0.308,
        "uncal_y": 0.326,
        "peak_nits": 135.0,
    },
    "sony_a95k_hdr": {
        "profile_name": "Sony A95K QD-OLED",
        "mode": "hdr",
        "eotf": "pq",
        "target_space": "p3d65",
        "code_max": 1023,
        "uncal_x": 0.314,
        "uncal_y": 0.330,
        "peak_nits": 1000.0,
    },
    "samsung_s95d_hdr": {
        "profile_name": "Samsung S95D QD-OLED",
        "mode": "hdr",
        "eotf": "pq",
        "target_space": "p3d65",
        "code_max": 1023,
        # Slightly warm uncalibrated WP
        "uncal_x": 0.315,
        "uncal_y": 0.332,
        "peak_nits": 1500.0,
    },
    "tcl_qm8_sdr": {
        "profile_name": "TCL QM8 Mini-LED",
        "mode": "sdr",
        "eotf": "bt1886",
        "target_space": "bt709",
        "code_max": 235,
        "uncal_x": 0.310,
        "uncal_y": 0.324,
        "peak_nits": 100.0,
    },
}


@pytest.fixture(params=list(TV_MODEL_PROFILES.keys()))
def tv_model(request) -> Dict[str, Any]:
    """Parametrized fixture — yields one TV model profile per test run.

    Example usage:
        def test_analysis_all_models(tv_model, sdr_grayscale_csv, hdr_grayscale_csv):
            csv = hdr_grayscale_csv if tv_model["mode"] == "hdr" else sdr_grayscale_csv
            ...
    """
    return TV_MODEL_PROFILES[request.param]


@pytest.fixture
def lg_c3_profile() -> Dict[str, Any]:
    """Single LG C3 SDR profile for non-parametrized tests."""
    return TV_MODEL_PROFILES["lg_c3_sdr"]


@pytest.fixture
def sony_a95k_profile() -> Dict[str, Any]:
    """Single Sony A95K HDR profile for non-parametrized tests."""
    return TV_MODEL_PROFILES["sony_a95k_hdr"]
