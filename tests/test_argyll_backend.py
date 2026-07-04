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

    def test_passes_correction_path_via_X_flag(self):
        """When correction_path is set, -X <path> must appear in the command."""
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        correction = "/home/user/ccmx/DisplayPlus_U8G.ccmx"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(correction_path=correction)

        cmd = mock_run.call_args.args[0]
        assert "-X" in cmd
        assert correction in cmd

    def test_correction_path_before_port_in_command(self):
        """-X comes before the port argument in the built command."""
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        correction = "/home/user/ccmx/DisplayPlus_U8G.ccmx"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(port="/dev/ttyUSB0", correction_path=correction)

        cmd = mock_run.call_args.args[0]
        x_idx = cmd.index("-X")
        port_idx = cmd.index("/dev/ttyUSB0")
        assert x_idx < port_idx

    def test_no_warning_when_correction_is_set(self):
        """Even if spotread output mentions ccmx/ccss, no warning field when
        the user explicitly provided a correction path."""
        stdout = (
            "No CCMX/CCSS correction file found for this instrument\n"
            "Result is XYZ: 1.0 2.0 3.0\n"
        )
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = argyll_backend.read_xyz(
                correction_path="/some/correction.ccmx"
            )

        assert result["ok"] is True
        assert "warning" not in result

    def test_warning_when_no_correction_and_output_mentions_ccmx(self):
        """When no correction path is set and spotread warns about missing
        CCMX/CCSS, the result includes an advisory warning field."""
        stdout = (
            "No CCMX/CCSS correction file found for this instrument\n"
            "Result is XYZ: 26.80 28.41 30.32\n"
        )
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = argyll_backend.read_xyz()

        assert result["ok"] is True
        assert "warning" in result
        assert "CCMX/CCSS" in result["warning"]
        assert "wide-gamut" in result["warning"]

    def test_no_warning_when_output_has_no_correction_markers(self):
        """Clean spotread output without any correction-related text should
        not include a warning field."""
        stdout = "Result is XYZ: 26.80 28.41 30.32\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)):
            result = argyll_backend.read_xyz()

        assert result["ok"] is True
        assert "warning" not in result


# ── read_xyz: refresh-timing / integration tuning (issue #600) ─────────────────

class TestReadXyzRefreshTiming:
    def test_default_unset_command_is_byte_identical_to_e_O(self):
        """No refresh_mode/refresh_rate_hz/integration_mode passed — no -Y flags
        of any kind, reproducing today's command exactly."""
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz()

        cmd = mock_run.call_args.args[0]
        assert cmd == ["/usr/bin/spotread", "-e", "-O"]

    def test_refresh_mode_refresh_passes_Y_r(self):
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(refresh_mode="refresh")

        cmd = mock_run.call_args.args[0]
        assert cmd == ["/usr/bin/spotread", "-e", "-O", "-Y", "r"]

    def test_refresh_mode_non_refresh_passes_Y_n(self):
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(refresh_mode="non_refresh")

        cmd = mock_run.call_args.args[0]
        assert cmd == ["/usr/bin/spotread", "-e", "-O", "-Y", "n"]

    def test_refresh_rate_hz_passes_Y_R_rate(self):
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(refresh_rate_hz=119.88)

        cmd = mock_run.call_args.args[0]
        assert cmd == ["/usr/bin/spotread", "-e", "-O", "-Y", "R:119.88"]

    def test_integration_mode_non_adaptive_passes_Y_A(self):
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(integration_mode="non_adaptive")

        cmd = mock_run.call_args.args[0]
        assert cmd == ["/usr/bin/spotread", "-e", "-O", "-Y", "A"]

    def test_integration_mode_averaging_passes_Y_a(self):
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(integration_mode="averaging")

        cmd = mock_run.call_args.args[0]
        assert cmd == ["/usr/bin/spotread", "-e", "-O", "-Y", "a"]

    def test_all_options_combine_before_port_in_documented_order(self):
        """-X, then the -Y sub-flags in refresh_mode/refresh_rate_hz/
        integration_mode order, then the trailing port — matching the
        existing correction_path -> -X ordering convention."""
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            argyll_backend.read_xyz(
                port="/dev/ttyUSB0",
                correction_path="/some/correction.ccmx",
                refresh_mode="refresh",
                refresh_rate_hz=120,
                integration_mode="averaging",
            )

        cmd = mock_run.call_args.args[0]
        assert cmd == [
            "/usr/bin/spotread", "-e", "-O",
            "-X", "/some/correction.ccmx",
            "-Y", "r",
            "-Y", "R:120",
            "-Y", "a",
            "/dev/ttyUSB0",
        ]

    def test_read_xyz_async_forwards_refresh_timing_params(self):
        stdout = "Result is XYZ: 1.0 2.0 3.0\n"
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=stdout)) as mock_run:
            asyncio.run(argyll_backend.read_xyz_async(
                refresh_mode="non_refresh",
                refresh_rate_hz=60,
                integration_mode="non_adaptive",
            ))

        cmd = mock_run.call_args.args[0]
        assert cmd == [
            "/usr/bin/spotread", "-e", "-O",
            "-Y", "n",
            "-Y", "R:60",
            "-Y", "A",
        ]

    def test_rejected_flag_fails_loudly_as_spotread_error(self):
        """If the installed Argyll version rejects a -Y flag, spotread's
        non-zero exit surfaces as a clear spotread_error, not a silent
        misread."""
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch(
                 "subprocess.run",
                 return_value=_mock_proc(
                     stderr="spotread: Unknown option 'q'\n", returncode=1
                 ),
             ):
            result = argyll_backend.read_xyz(refresh_mode="refresh")

        assert result["ok"] is False
        assert result["error_type"] == "spotread_error"
        assert "raw" in result


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


# ── list_instruments: meter discovery (issue #531) ──────────────────────────────

_SPOTREAD_HELP_TWO_INSTRUMENTS = """
usage: spotread [-options] [outfile]
 -v                   Verbose mode
 -c listno            Set communication port from the following list (default 1)
    1 = 'Klein K10-A on COM3'
    2 = 'X-Rite i1 Display Pro, USB'
 -e                   Treat display as an emissive source
"""

_SPOTREAD_HELP_NO_INSTRUMENTS = """
usage: spotread [-options] [outfile]
 -v                   Verbose mode
 -c listno            Set communication port from the following list (default 1)
 -e                   Treat display as an emissive source
"""


class TestListInstruments:
    def test_parses_multiple_instruments(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=_SPOTREAD_HELP_TWO_INSTRUMENTS, returncode=1)):
            result = argyll_backend.list_instruments()

        assert result["ok"] is True
        assert result["instruments"] == [
            {"index": 1, "name": "Klein K10-A on COM3"},
            {"index": 2, "name": "X-Rite i1 Display Pro, USB"},
        ]

    def test_no_instruments_connected_is_a_valid_empty_result(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=_SPOTREAD_HELP_NO_INSTRUMENTS, returncode=1)):
            result = argyll_backend.list_instruments()

        assert result["ok"] is True
        assert result["instruments"] == []

    def test_not_found(self):
        with patch("shutil.which", return_value=None), \
             patch("os.path.isfile", return_value=False):
            result = argyll_backend.list_instruments()

        assert result["ok"] is False
        assert result["error_type"] == "not_found"

    def test_timeout(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch(
                 "subprocess.run",
                 side_effect=subprocess.TimeoutExpired(cmd="spotread", timeout=5.0),
             ):
            result = argyll_backend.list_instruments(timeout=5.0)

        assert result["ok"] is False
        assert result["error_type"] == "timeout"

    def test_list_instruments_async_delegates_to_threadpool(self):
        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=_SPOTREAD_HELP_TWO_INSTRUMENTS, returncode=1)):
            result = asyncio.run(argyll_backend.list_instruments_async())

        assert result["ok"] is True
        assert len(result["instruments"]) == 2


# ── bridge.py /instruments and /config/argyll-port endpoints ───────────────────

class TestBridgeInstrumentEndpoints:
    def setup_method(self):
        bridge._config = dict(bridge.DEFAULT_CONFIG)

    def test_get_instruments_requires_argyll_backend(self):
        bridge._config["backend"] = "pyautogui"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.get("/instruments")

        resp = asyncio.run(_run())
        assert resp.status_code == 400

    def test_get_instruments_lists_detected_meters(self):
        bridge._config["backend"] = "argyll"
        bridge._config["argyll_port"] = "2"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.get("/instruments")

        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=_SPOTREAD_HELP_TWO_INSTRUMENTS, returncode=1)):
            resp = asyncio.run(_run())

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["instruments"]) == 2
        assert data["selected_port"] == "2"

    def test_set_argyll_port_updates_runtime_config(self):
        bridge._config["backend"] = "argyll"
        assert bridge._config.get("argyll_port") is None

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.post("/config/argyll-port", json={"port": "2"})

        resp = asyncio.run(_run())

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "argyll_port": "2"}
        assert bridge._config["argyll_port"] == "2"

    def test_set_argyll_correction_path_updates_runtime_config(self):
        bridge._config["backend"] = "argyll"
        assert bridge._config.get("argyll_correction") is None

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.post(
                    "/config/argyll-port",
                    json={"port": "2", "correction_path": "/etc/argyll/i1d3.ccss"},
                )

        resp = asyncio.run(_run())

        assert resp.status_code == 200
        assert resp.json() == {
            "ok": True,
            "argyll_port": "2",
            "argyll_correction": "/etc/argyll/i1d3.ccss",
        }
        assert bridge._config["argyll_port"] == "2"
        assert bridge._config["argyll_correction"] == "/etc/argyll/i1d3.ccss"

    def test_set_argyll_port_omits_correction_key_when_not_given(self):
        """Omitting correction_path leaves the existing correction untouched
        and the response doesn't grow an unrequested key (back-compat)."""
        bridge._config["backend"] = "argyll"
        bridge._config["argyll_correction"] = "/etc/argyll/existing.ccmx"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.post("/config/argyll-port", json={"port": "3"})

        resp = asyncio.run(_run())

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "argyll_port": "3"}
        assert bridge._config["argyll_correction"] == "/etc/argyll/existing.ccmx"

    def test_instruments_reports_selected_correction_path(self):
        bridge._config["backend"] = "argyll"
        bridge._config["argyll_correction"] = "/etc/argyll/i1d3.ccss"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.get("/instruments")

        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=_SPOTREAD_HELP_TWO_INSTRUMENTS, returncode=1)):
            resp = asyncio.run(_run())

        assert resp.status_code == 200
        assert resp.json()["correction_path"] == "/etc/argyll/i1d3.ccss"

    def test_set_argyll_port_takes_effect_on_next_measure(self):
        """The runtime override actually drives the next read, not just the config dict."""
        bridge._config["backend"] = "argyll"

        captured_cmds = []

        def recording_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return _mock_proc(stdout="Result is XYZ: 1.0 2.0 3.0\n")

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                await client.post("/config/argyll-port", json={"port": "2"})
                return await client.post("/measure")

        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", side_effect=recording_run):
            resp = asyncio.run(_run())

        assert resp.status_code == 200
        assert captured_cmds[-1][-1] == "2"

    def test_set_argyll_refresh_timing_updates_runtime_config(self):
        bridge._config["backend"] = "argyll"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.post(
                    "/config/argyll-port",
                    json={
                        "port": "2",
                        "refresh_mode": "refresh",
                        "refresh_rate_hz": 119.88,
                        "integration_mode": "averaging",
                    },
                )

        resp = asyncio.run(_run())

        assert resp.status_code == 200
        assert resp.json() == {
            "ok": True,
            "argyll_port": "2",
            "refresh_mode": "refresh",
            "refresh_rate_hz": 119.88,
            "integration_mode": "averaging",
        }
        assert bridge._config["argyll_refresh_mode"] == "refresh"
        assert bridge._config["argyll_refresh_rate_hz"] == 119.88
        assert bridge._config["argyll_integration_mode"] == "averaging"

    def test_set_argyll_refresh_mode_rejects_invalid_value(self):
        bridge._config["backend"] = "argyll"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.post(
                    "/config/argyll-port",
                    json={"port": "2", "refresh_mode": "bogus"},
                )

        resp = asyncio.run(_run())
        assert resp.status_code == 400

    def test_set_argyll_integration_mode_rejects_invalid_value(self):
        bridge._config["backend"] = "argyll"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.post(
                    "/config/argyll-port",
                    json={"port": "2", "integration_mode": "bogus"},
                )

        resp = asyncio.run(_run())
        assert resp.status_code == 400

    def test_partial_refresh_settings_updates_persist_unmodified_fields(self):
        """Setting refresh_mode in one request, then refresh_rate_hz in a
        separate request that omits refresh_mode, must not clobber the
        value set by the first request (same omit-a-key-to-leave-it-alone
        contract as correction_path)."""
        bridge._config["backend"] = "argyll"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                await client.post("/config/argyll-port", json={"port": "1", "refresh_mode": "refresh"})
                return await client.post("/config/argyll-port", json={"port": "1", "refresh_rate_hz": 120})

        resp = asyncio.run(_run())

        assert resp.status_code == 200
        assert bridge._config["argyll_refresh_mode"] == "refresh"
        assert bridge._config["argyll_refresh_rate_hz"] == 120

    def test_instruments_reports_refresh_timing_fields(self):
        bridge._config["backend"] = "argyll"
        bridge._config["argyll_refresh_mode"] = "non_refresh"
        bridge._config["argyll_refresh_rate_hz"] = 60.0
        bridge._config["argyll_integration_mode"] = "non_adaptive"

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                return await client.get("/instruments")

        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", return_value=_mock_proc(stdout=_SPOTREAD_HELP_TWO_INSTRUMENTS, returncode=1)):
            resp = asyncio.run(_run())

        assert resp.status_code == 200
        data = resp.json()
        assert data["refresh_mode"] == "non_refresh"
        assert data["refresh_rate_hz"] == 60.0
        assert data["integration_mode"] == "non_adaptive"

    def test_set_argyll_refresh_timing_takes_effect_on_next_measure(self):
        bridge._config["backend"] = "argyll"

        captured_cmds = []

        def recording_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return _mock_proc(stdout="Result is XYZ: 1.0 2.0 3.0\n")

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                await client.post("/config/argyll-port", json={"port": None, "refresh_mode": "refresh"})
                return await client.post("/measure")

        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", side_effect=recording_run):
            resp = asyncio.run(_run())

        assert resp.status_code == 200
        assert captured_cmds[-1] == ["/usr/bin/spotread", "-e", "-O", "-Y", "r"]

    def test_config_write_concurrent_with_in_flight_measure_does_not_race(self):
        """
        A POST /config/argyll-port landing while a /measure is already
        in-flight must not crash or corrupt state. _config["argyll_port"] is
        a single dict-key assignment (atomic under the GIL, no read-modify-
        write), so the only real question is which value an in-flight read
        sees — this pins that down: whichever port was set at the moment
        /measure read _config, before the concurrent write.
        """
        bridge._config["backend"] = "argyll"
        bridge._config["argyll_port"] = "1"

        captured_cmds = []

        def slow_recording_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            time.sleep(0.05)
            return _mock_proc(stdout="Result is XYZ: 1.0 2.0 3.0\n")

        async def _run():
            transport = httpx.ASGITransport(app=bridge.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
                measure_task = asyncio.create_task(client.post("/measure"))
                await asyncio.sleep(0.01)  # let /measure read _config before we mutate it
                config_task = asyncio.create_task(
                    client.post("/config/argyll-port", json={"port": "2"})
                )
                return await asyncio.gather(measure_task, config_task)

        with patch("shutil.which", return_value="/usr/bin/spotread"), \
             patch("subprocess.run", side_effect=slow_recording_run):
            measure_resp, config_resp = asyncio.run(_run())

        assert measure_resp.status_code == 200
        assert config_resp.status_code == 200
        # The in-flight measure used the port that was selected when it was
        # dispatched, unaffected by the write that landed after.
        assert captured_cmds[-1][-1] == "1"
        # The concurrent write still lands cleanly for the *next* read.
        assert bridge._config["argyll_port"] == "2"
