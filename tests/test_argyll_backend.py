"""
Tests for the ArgyllCMS spotread backend (tools/zro-bridge/backends/argyll_backend.py).

Mocks subprocess.run so no real ArgyllCMS installation or meter is required.
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "zro-bridge"))

import bridge  # noqa: E402
from backends import argyll_backend  # noqa: E402


def _mock_proc(stdout="", stderr="", returncode=0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


# ── find_spotread ────────────────────────────────────────────────────────────────

class TestFindSpotread:
    def test_resolves_via_path(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"):
            assert argyll_backend.find_spotread("spotread") == "/usr/bin/spotread"

    def test_resolves_explicit_file_path(self):
        with patch("shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=True):
            assert argyll_backend.find_spotread("/opt/argyll/bin/spotread") == "/opt/argyll/bin/spotread"

    def test_not_found(self):
        with patch("shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=False):
            assert argyll_backend.find_spotread("spotread") is None


# ── read_xyz: success parsing ───────────────────────────────────────────────────

class TestReadXyzParsing:
    def test_parses_xyz_result_line(self):
        stdout = (
            "Place instrument on spot to be measured\n"
            "Result is XYZ: 26.799524 28.406951 30.317612, "
            "D50 Lab: 60.245047 -1.014627 -3.061203\n"
        )
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = argyll_backend.read_xyz()

        assert result["ok"] is True
        assert result["method"] == "argyll"
        assert result["X"] == pytest.approx(26.799524)
        assert result["Y"] == pytest.approx(28.406951)
        assert result["Z"] == pytest.approx(30.317612)
        assert 0.0 <= result["x"] <= 1.0
        assert 0.0 <= result["y"] <= 1.0

    def test_parses_yxy_result_line(self):
        stdout = "Result is Yxy: 100.000000 0.312700 0.329000\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = argyll_backend.read_xyz()

        assert result["ok"] is True
        assert result["Y"] == pytest.approx(100.0)
        assert result["x"] == pytest.approx(0.3127, abs=1e-4)
        assert result["y"] == pytest.approx(0.329, abs=1e-4)

    def test_passes_port_argument(self):
        stdout = "Result is XYZ: 1.0 1.0 1.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(port="/dev/ttyUSB0")

        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "/usr/bin/spotread"
        assert "-e" in cmd
        assert "-O" in cmd
        assert cmd[-1] == "/dev/ttyUSB0"


# ── read_xyz: HDR absolute-nit verification (issue #534) ───────────────────────

class TestAbsoluteNits:
    def test_hdr_luminance_passed_through_as_absolute_cdm2(self):
        """A known HDR-range Y (nits, not normalized 0-1 or 0-100) must come
        back unchanged — spotread's emissive mode is already absolute."""
        known_nits = 734.512
        stdout = f"Result is XYZ: 680.220000 {known_nits:.6f} 810.100000\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = argyll_backend.read_xyz()

        assert result["ok"] is True
        assert result["Y"] == pytest.approx(known_nits)
        # Not normalized to a 0-1 or 0-100 range.
        assert result["Y"] > 100.0

    def test_sdr_reference_white_also_passes_through_unscaled(self):
        stdout = "Result is XYZ: 95.05 100.00 108.90\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = argyll_backend.read_xyz()

        assert result["Y"] == pytest.approx(100.0)


# ── read_xyz: error paths (issue #536) ──────────────────────────────────────────

class TestReadXyzErrors:
    def test_spotread_not_found(self):
        with patch("shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=False):
            result = argyll_backend.read_xyz()

        assert result["ok"] is False
        assert result["error_type"] == "not_found"
        assert "not found" in result["error"].lower()

    def test_no_meter_detected(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch(
                 "subprocess.run",
                 return_value=_mock_proc(
                     stderr="Instrument Access Failed: No suitable device found\n",
                     returncode=1,
                 ),
             ):
            result = argyll_backend.read_xyz()

        assert result["ok"] is False
        assert result["error_type"] == "no_meter"

    def test_timeout(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch(
                 "subprocess.run",
                 side_effect=subprocess.TimeoutExpired(cmd="spotread", timeout=20.0),
             ):
            result = argyll_backend.read_xyz(timeout=20.0)

        assert result["ok"] is False
        assert result["error_type"] == "timeout"

    def test_binary_disappears_between_resolve_and_exec(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", side_effect=FileNotFoundError()):
            result = argyll_backend.read_xyz()

        assert result["ok"] is False
        assert result["error_type"] == "not_found"

    def test_unparsable_output(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout="unexpected garbage\n")):
            result = argyll_backend.read_xyz()

        assert result["ok"] is False
        assert result["error_type"] == "parse_error"
        assert "raw" in result

    def test_generic_nonzero_exit(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch(
                 "subprocess.run",
                 return_value=_mock_proc(stderr="Some other spotread failure\n", returncode=2),
             ):
            result = argyll_backend.read_xyz()

        assert result["ok"] is False
        assert result["error_type"] == "spotread_error"


# ── read_xyz_async: off-event-loop execution (issue #533) ──────────────────────

class TestReadXyzAsync:
    def test_delegates_to_threadpool(self):
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = asyncio.run(argyll_backend.read_xyz_async(port="COM3", timeout=5.0))

        assert result["ok"] is True
        assert result["Y"] == pytest.approx(2.0)

    def test_runs_via_run_in_threadpool(self):
        """The blocking spotread call must go through run_in_threadpool, not
        be awaited/executed directly on the event loop."""

        async def _fake_pool(func, *args, **kwargs):
            return {"ok": True, "method": "argyll", "func": func}

        with patch("backends.argyll_backend.run_in_threadpool", side_effect=_fake_pool) as mock_pool:
            result = asyncio.run(argyll_backend.read_xyz_async(port="COM3"))

        mock_pool.assert_called_once()
        assert result["func"] is argyll_backend.read_xyz


# ── bridge.py /measure integration: concurrent argyll reads (issue #533) ───────

class TestConcurrentArgyllMeasureRequests:
    def test_two_concurrent_reads_overlap_instead_of_serializing(self):
        """
        End-to-end proof that two simultaneous POST /measure calls
        (backend=argyll) run concurrently through Starlette's threadpool
        rather than blocking each other on the event loop: two slow
        "meter reads" complete in roughly one read's duration, not two.
        """
        read_duration = 0.15

        def slow_subprocess_run(*args, **kwargs):
            time.sleep(read_duration)
            return _mock_proc(stdout="Result is XYZ: 1.0 2.0 3.0\n")

        bridge._config = dict(bridge.DEFAULT_CONFIG)
        bridge._config["backend"] = "argyll"

        async def _fire_concurrent_requests():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                start = time.monotonic()
                responses = await asyncio.gather(
                    client.post("/measure"),
                    client.post("/measure"),
                )
                return responses, time.monotonic() - start

        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", side_effect=slow_subprocess_run):
            responses, elapsed = asyncio.run(_fire_concurrent_requests())

        for resp in responses:
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

        # Serialized on the event loop, two reads would take ~2x read_duration.
        # Run concurrently via run_in_threadpool, total wall time stays close to 1x.
        assert elapsed < read_duration * 1.6
