from __future__ import annotations

import csv
import io
import os
import re
from typing import List, Literal

from .colour import xyY_to_xyz
from .models import Patch


def parse_float(s: str) -> float:
    return float(s.strip().replace("ms", ""))


def parse_int(s: str) -> int:
    return int(float(s.strip()))


def is_number(s: str) -> bool:
    try:
        float(s.strip().replace("ms", ""))
        return True
    except Exception:
        return False


def _classify_headerless_row(
    v4: float, v5: float, v6: float, explicit_format: Literal["xyY", "XYZ"] | None,
) -> tuple[tuple[float, float, float], tuple[float, float, float] | None]:
    """Return (meas_xyz, meas_yxy) for a headerless row.

    When *explicit_format* is given, use it directly.
    Otherwise apply the tightened heuristic:
      - Treat as xyY only when x + y < 1.0 (valid chromaticity triangle boundary).
      - Otherwise treat as XYZ.
    """
    if explicit_format == "XYZ":
        return (v4, v5, v6), None

    if explicit_format == "xyY":
        Y, x, y = v4, v5, v6
        return xyY_to_xyz(x, y, Y), (Y, x, y)

    # Auto-detect: xyY is valid only when x + y < 1.0 (chromaticity inside triangle).
    # This prevents low-luminance XYZ values (e.g. black: X=0.3, Y=0.5, Z=0.4)
    # from being misclassified as xyY.
    x, y, Y = v5, v6, v4
    if 0 <= x <= 1.0 and 0 <= y <= 1.0 and Y >= 0 and (x + y) < 1.0:
        return xyY_to_xyz(x, y, Y), (Y, x, y)

    return (v4, v5, v6), None


def parse_measurement_csv(
    source: str | bytes | io.IOBase,
    *,
    format: Literal["xyY", "XYZ", None] = None,
) -> List[Patch]:
    patches: List[Patch] = []
    # Normalize input to raw string lines
    if isinstance(source, bytes):
        raw = source.decode("utf-8-sig")
    elif isinstance(source, str):
        # Distinguish between a file path and raw CSV content.
        # Use os.path.exists() so paths containing commas are handled correctly.
        if "\n" not in source and os.path.exists(source):
            with open(source, "r", newline="", encoding="utf-8-sig") as f:
                raw = f.read()
        else:
            raw = source
    else:
        raw = source.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig")
    raw_lines = raw.splitlines()

    first_nonempty = next((line for line in raw_lines if line.strip()), "")
    header_like = bool(re.search(r"[A-Za-z]", first_nonempty)) and "," in first_nonempty

    if header_like:
        reader = csv.DictReader(raw_lines)
        for row in reader:
            keys = {
                k.lower().strip(): v
                for k, v in row.items()
                if k is not None and v is not None
            }
            if not keys:
                continue

            rt = keys.get("r_target") or keys.get("stimulus_r") or keys.get("r")
            gt = keys.get("g_target") or keys.get("stimulus_g") or keys.get("g")
            bt = keys.get("b_target") or keys.get("stimulus_b") or keys.get("b")
            if rt is None or gt is None or bt is None:
                continue

            r = parse_int(rt)
            g = parse_int(gt)
            b = parse_int(bt)

            if all(k in keys for k in ("x", "y", "z")):
                meas_xyz = (
                    parse_float(keys["x"]),
                    parse_float(keys["y"]),
                    parse_float(keys["z"]),
                )
                meas_yxy = None
            elif all(k in keys for k in ("y_measured", "x_measured", "y_measured_2")):
                Y = parse_float(keys["y_measured"])
                x = parse_float(keys["x_measured"])
                y = parse_float(keys["y_measured_2"])
                meas_xyz = xyY_to_xyz(x, y, Y)
                meas_yxy = (Y, x, y)
            elif all(k in keys for k in ("y", "x", "y2")):
                Y = parse_float(keys["y"])
                x = parse_float(keys["x"])
                y = parse_float(keys["y2"])
                meas_xyz = xyY_to_xyz(x, y, Y)
                meas_yxy = (Y, x, y)
            else:
                continue

            patches.append(
                Patch(
                    label=f"{r},{g},{b}",
                    r_target=r,
                    g_target=g,
                    b_target=b,
                    meas_xyz=meas_xyz,
                    meas_yxy=meas_yxy,
                    kind="grayscale" if r == g == b else "color",
                )
            )
        return patches

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        if not all(is_number(parts[i]) for i in (1, 2, 3, 4, 5, 6)):
            continue

        r = parse_int(parts[1])
        g = parse_int(parts[2])
        b = parse_int(parts[3])
        v4 = parse_float(parts[4])
        v5 = parse_float(parts[5])
        v6 = parse_float(parts[6])

        meas_xyz, meas_yxy = _classify_headerless_row(v4, v5, v6, format)

        patches.append(
            Patch(
                label=f"{r},{g},{b}",
                r_target=r,
                g_target=g,
                b_target=b,
                meas_xyz=meas_xyz,
                meas_yxy=meas_yxy,
                kind="grayscale" if r == g == b else "color",
            )
        )

    return patches
