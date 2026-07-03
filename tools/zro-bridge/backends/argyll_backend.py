"""
ArgyllCMS direct-meter backend — wraps ``spotread`` for a license-free
colorimeter/spectrophotometer read (i1Display, i1Pro, Klein K10-A, Colorimetry
Research, etc.), removing ColourSpace ZRO from the measurement loop entirely.

Install ArgyllCMS (free, open source): https://www.argyllcms.com/
``spotread`` ships in the release's ``bin/`` directory — either put it on
PATH or set ``argyll_spotread_path`` in bridge.json to the full executable
path. On Linux the meter needs udev permissions (see ArgyllCMS's ``usb/``
rule files); on Windows install the instrument driver that ships with Argyll.

Reads are single-shot emissive (``spotread -e -O``). Argyll's emissive mode
reports **absolute** XYZ — ``Y`` is real luminance in cd/m^2, not normalized
to a 0-100 or 0-1 white point — which is exactly what PQ/ST.2084 HDR targets
need and must be preserved end to end (see ``read_xyz``'s docstring).

CCMX/CCSS spectral correction (issue #532) is separate follow-up work.
"""

import logging
import os
import re
import shutil
import subprocess
from typing import Literal, NotRequired, Optional, Tuple, TypedDict, Union

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


class XyzReadOk(TypedDict):
    ok: Literal[True]
    method: str
    X: float
    Y: float  # absolute cd/m^2 — see read_xyz()'s docstring
    Z: float
    x: float
    y: float
    raw: str


ErrorType = Literal["not_found", "no_meter", "timeout", "parse_error", "spotread_error"]


class XyzReadError(TypedDict):
    ok: Literal[False]
    error: str
    error_type: ErrorType
    raw: NotRequired[str]


XyzReadResult = Union[XyzReadOk, XyzReadError]


class Instrument(TypedDict):
    index: int
    name: str


class ListInstrumentsOk(TypedDict):
    ok: Literal[True]
    instruments: list[Instrument]
    raw: str


class ListInstrumentsError(TypedDict):
    ok: Literal[False]
    error: str
    error_type: ErrorType
    raw: NotRequired[str]


ListInstrumentsResult = Union[ListInstrumentsOk, ListInstrumentsError]

_DEFAULT_TIMEOUT_S = 20.0

# spotread prints the reading result on stdout as a line such as:
#   Result is XYZ: 26.799524 28.406951 30.317612, D50 Lab: 60.245047 -1.014627 -3.061203
# or, if configured for xyY output:
#   Result is Yxy: 28.406951 0.345670 0.358500
_XYZ_RESULT_RE = re.compile(
    r"Result is XYZ:\s*([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)", re.IGNORECASE
)
_YXY_RESULT_RE = re.compile(
    r"Result is Yxy:\s*([-+\d.]+)\s+([-+\d.]+)\s+([-+\d.]+)", re.IGNORECASE
)

# Substrings spotread prints on stdout/stderr when no instrument is attached
# or communication fails, used to classify the failure for the caller.
_NO_METER_MARKERS = (
    "no such file",
    "no serial ports",
    "unable to open",
    "not found on any port",
    "instrument access failed",
    "no suitable device",
    "no ccmx/ccss",
)

# `spotread -?` prints its usage text, which includes the -c option's
# numbered list of currently detected communication ports/instruments, e.g.:
#   -c listno       Set communication port from the following list (default 1)
#       1 = 'Klein K10-A on COM3'
#       2 = 'X-Rite i1 Display Pro, USB'
# This is how every Argyll CLI tool (dispcal, spotread, dispwin, ...) surfaces
# instrument discovery — there's no separate "list instruments" subcommand.
_INSTRUMENT_LINE_RE = re.compile(r"^\s*(\d+)\s*=\s*'([^']+)'\s*$", re.MULTILINE)


def find_spotread(explicit_path: Optional[str] = "spotread") -> Optional[str]:
    """
    Locate the spotread executable.

    Returns the resolved path, or None if it cannot be found on PATH or as a
    direct file path.
    """
    candidate = explicit_path or "spotread"
    resolved = shutil.which(candidate)
    if resolved:
        return resolved
    if os.path.isfile(candidate):
        return candidate
    return None


def _parse_result(output: str) -> Optional[dict]:
    """Parse a spotread result line into {'X', 'Y', 'Z'} (Y in cd/m^2)."""
    m = _XYZ_RESULT_RE.search(output)
    if m:
        X, Y, Z = (float(v) for v in m.groups())
        return {"X": X, "Y": Y, "Z": Z}

    m = _YXY_RESULT_RE.search(output)
    if m:
        Y, x, y = (float(v) for v in m.groups())
        if y == 0:
            return None
        X = (x / y) * Y
        Z = ((1 - x - y) / y) * Y
        return {"X": X, "Y": Y, "Z": Z}

    return None


def _xyz_to_xy(X: float, Y: float, Z: float) -> Tuple[float, float]:
    total = X + Y + Z
    if total <= 0:
        return (0.0, 0.0)
    return (X / total, Y / total)


def read_xyz(
    port: Optional[str] = None,
    *,
    spotread_path: str = "spotread",
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> XyzReadResult:
    """
    Take one single-shot emissive reading via ``spotread -e -O`` and return XYZ.

    Install ArgyllCMS from https://www.argyllcms.com/; on Linux the meter
    needs udev permissions (see the module docstring for details).

    ``Y`` is absolute luminance in cd/m^2 (nits), taken as-is from spotread's
    emissive output — never rescaled or normalized. This is required for
    HDR/PQ targets, which compare against absolute-nit references.

    ``timeout`` bounds a single read only (this function issues exactly one
    ``spotread`` call). It is not a budget for a measurement sequence —
    callers driving multiple reads (e.g. ``/measure/sequence``) apply it once
    per call. High-end spectrophotometers doing multi-flash averaging may
    need longer than the ``_DEFAULT_TIMEOUT_S`` default; raise
    ``argyll_timeout_s`` in bridge.json if reads are timing out on slow
    hardware.

    Returns on success::

        {"ok": True, "method": "argyll", "X": .., "Y": .., "Z": .., "x": .., "y": ..,
         "raw": "<spotread stdout>"}

    Returns on failure::

        {"ok": False, "error": "...",
         "error_type": "not_found" | "no_meter" | "timeout" | "parse_error" | "spotread_error"}
    """
    resolved = find_spotread(spotread_path)
    if resolved is None:
        return {
            "ok": False,
            "error_type": "not_found",
            "error": (
                f"ArgyllCMS spotread not found ({spotread_path!r}). Install ArgyllCMS "
                "(https://www.argyllcms.com/) and put spotread on PATH, or set "
                "argyll_spotread_path in bridge.json."
            ),
        }

    cmd = [resolved, "-e", "-O"]
    if port:
        cmd.append(port)

    logger.info("Running: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error_type": "not_found",
            "error": f"ArgyllCMS spotread not found at {resolved!r}.",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_type": "timeout",
            "error": f"spotread did not respond within {timeout}s. Check the meter connection.",
        }

    output = f"{proc.stdout}\n{proc.stderr}"

    if proc.returncode != 0:
        lowered = output.lower()
        if any(marker in lowered for marker in _NO_METER_MARKERS):
            return {
                "ok": False,
                "error_type": "no_meter",
                "error": (
                    "No colorimeter/spectrophotometer detected. Check the USB "
                    "connection and (on Linux) udev permissions."
                ),
                "raw": output.strip(),
            }
        return {
            "ok": False,
            "error_type": "spotread_error",
            "error": f"spotread exited with code {proc.returncode}.",
            "raw": output.strip(),
        }

    parsed = _parse_result(output)
    if parsed is None:
        return {
            "ok": False,
            "error_type": "parse_error",
            "error": "Could not parse XYZ from spotread output.",
            "raw": output.strip(),
        }

    x, y = _xyz_to_xy(parsed["X"], parsed["Y"], parsed["Z"])
    return {
        "ok": True,
        "method": "argyll",
        "X": parsed["X"],
        "Y": parsed["Y"],
        "Z": parsed["Z"],
        "x": x,
        "y": y,
        "raw": output.strip(),
    }


async def read_xyz_async(
    port: Optional[str] = None,
    *,
    spotread_path: str = "spotread",
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> XyzReadResult:
    """
    Async wrapper around ``read_xyz()``.

    ``spotread`` blocks for the duration of the meter read (potentially
    several seconds); run it in FastAPI/Starlette's threadpool so it never
    blocks the event loop, consistent with the async I/O pattern already
    used for blocking calls elsewhere in the app (``server.py``'s
    ``run_in_threadpool`` usage).
    """
    return await run_in_threadpool(
        read_xyz, port, spotread_path=spotread_path, timeout=timeout
    )


def list_instruments(
    *,
    spotread_path: str = "spotread",
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> ListInstrumentsResult:
    """
    Enumerate the communication ports/instruments spotread currently detects,
    by parsing the numbered ``-c listno`` list out of ``spotread -?``'s usage
    text (see ``_INSTRUMENT_LINE_RE``).

    An empty ``instruments`` list with ``ok: True`` is a normal, valid result
    — it means spotread ran fine but nothing is plugged in, not an error.
    Use the returned ``index`` as the ``port`` argument to ``read_xyz()``.
    """
    resolved = find_spotread(spotread_path)
    if resolved is None:
        return {
            "ok": False,
            "error_type": "not_found",
            "error": (
                f"ArgyllCMS spotread not found ({spotread_path!r}). Install ArgyllCMS "
                "(https://www.argyllcms.com/) and put spotread on PATH, or set "
                "argyll_spotread_path in bridge.json."
            ),
        }

    cmd = [resolved, "-?"]
    logger.info("Running: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error_type": "not_found",
            "error": f"ArgyllCMS spotread not found at {resolved!r}.",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_type": "timeout",
            "error": f"spotread did not respond within {timeout}s.",
        }

    # spotread's usage/help commonly exits non-zero; that's not a failure
    # here — we only care whether the -c listno block parses.
    output = f"{proc.stdout}\n{proc.stderr}"
    instruments = [
        {"index": int(index_str), "name": name.strip()}
        for index_str, name in _INSTRUMENT_LINE_RE.findall(output)
    ]
    return {"ok": True, "instruments": instruments, "raw": output.strip()}


async def list_instruments_async(
    *,
    spotread_path: str = "spotread",
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> ListInstrumentsResult:
    """Async wrapper around ``list_instruments()`` — see ``read_xyz_async()``."""
    return await run_in_threadpool(
        list_instruments, spotread_path=spotread_path, timeout=timeout
    )
