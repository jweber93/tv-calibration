"""
Colorspace ZRO CSV import and classifier.

Parses the tab-separated measurement export from Colorspace ZRO (Portrait
Displays) and classifies each row into the session buckets used by the
calibration assistant:

    cms_measurements   — primary and secondary colour patches
    pre_measurements   — first grayscale ramp (pre-calibration)
    post_measurements  — second grayscale ramp (if present, post-calibration)
    lum_measurements   — 100% white (luminance reference)
    wb_measurements    — 80% gray (gain) and 30% gray (offset)
    gamma_measurements — snapped grayscale points used for gamma tracking

ZRO CSV format
--------------
Tab-separated, one header row, columns:

    Date and time | R | G | B | Y | x | y | msec

    - R, G, B are the exported signal values. They may be 8-bit range values
      (for example 16..235 or 0..255) or full-range 10-bit values
      (0..1023) depending on the active patch-generator path.
    - Y  = luminance in cd/m² (nits)
    - x, y = CIE 1931 chromaticity
    - msec = integration time (ignored by this parser)

Session-break detection
-----------------------
A gap of more than SESSION_BREAK_SECONDS between consecutive rows is treated
as a new measurement session.  ZRO typically shows ~1–2 minute gaps when the
user starts a new workflow (e.g. colours → grayscale).  Multiple grayscale
sessions map to pre_measurements (first) and post_measurements (second).
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from calcore.models import Measurement

from .utils import stimulus_pct_from_code_value

# ---------------------------------------------------------------------------
# Tuneable constants
# ---------------------------------------------------------------------------

SESSION_BREAK_SECONDS = 45
"""Seconds between rows that triggers a new session group."""

CHANNEL_ON_THRESHOLD = 228
"""R/G/B value at or above this is treated as "fully on" for classification."""

CHANNEL_OFF_THRESHOLD = 20
"""R/G/B value at or below this is treated as "fully off" for classification."""

# 10-bit equivalents used when ColourSpace ZRO exports full-range 10-bit code values
# (detected when max(r, g, b) > 255).  Named constants prevent magic-number drift and
# make the classification thresholds reviewable against the 0–1023 range.
BIT10_MAX = 1023
BIT10_ON_THRESHOLD = 900  # ~88% of 1023 — channel treated as "fully on"
BIT10_OFF_THRESHOLD = 80  # ~8%  of 1023 — channel treated as "fully off"
BIT10_WHITE_THRESHOLD = (
    980  # ~95.8% of 1023 — catches near-white patches (>1000 would miss 1001–1023)
)
BIT10_BLACK_THRESHOLD = (
    20  # footroom guard — avoids misclassifying limited-range 0 as black
)

GRAYSCALE_EQUAL_TOLERANCE = 4
"""Max channel-to-channel difference for a patch to be classified as grayscale."""

# Grayscale stimulus levels used by the calibration workflow (% of signal range)
WB_GAIN_TARGET_PCT = 80.0  # ~204 / 255
WB_OFFSET_TARGET_PCT = 30.0  # ~77  / 255
GAMMA_TARGET_PCTS = tuple(float(pct) for pct in range(5, 100, 5))
PCT_SNAP_TOLERANCE = 7.0  # ±% to snap a grayscale step to a target

# ---------------------------------------------------------------------------
# Public result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ZROImportResult:
    """Classified measurements ready to be merged into a session."""

    # Error state
    fatal_error: Optional[str] = None

    # Primary session buckets
    cms_measurements: List[dict] = field(default_factory=list)
    pre_measurements: List[dict] = field(default_factory=list)
    mid_measurements: List[dict] = field(default_factory=list)
    post_measurements: List[dict] = field(default_factory=list)
    lum_measurements: List[dict] = field(default_factory=list)
    wb_measurements: List[dict] = field(default_factory=list)
    gamma_measurements: List[dict] = field(default_factory=list)
    grayscale_passes: List[List[dict]] = field(default_factory=list)
    grayscale_pass_warnings: List[List[str]] = field(default_factory=list)
    raw_rows: List[dict] = field(default_factory=list)

    # Diagnostic metadata
    total_rows: int = 0
    sessions_detected: int = 0
    grayscale_sessions: int = 0  # how many grayscale ramps were found
    colour_sessions: int = 0  # how many colour-patch groups were found
    session_breaks: List[str] = field(default_factory=list)  # ISO timestamps

    # ABL / anomaly warnings
    abl_warnings: List[str] = field(default_factory=list)
    unknown_rows: int = 0  # rows that could not be classified
    row_errors: List[str] = field(default_factory=list)  # per-row diagnostic (capped at 20)


# ---------------------------------------------------------------------------
# Internal row representation
# ---------------------------------------------------------------------------


@dataclass
class _Row:
    dt: datetime
    r: int
    g: int
    b: int
    Y: float
    x: float
    y: float


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_datetime(raw: str) -> datetime:
    """Parse ZRO's timestamp formats, including sub-second precision."""
    raw = raw.strip()
    for fmt in (
        "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S.%f",
        "%m/%d/%Y %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised ZRO timestamp format: {raw!r}")


def _rgb_to_stimulus_pct(val: int) -> float:
    """
    Convert a ZRO RGB value to a 0–100 stimulus percentage.

    Delegates to the shared colour helper so importer bucketing handles both
    full-range-ish grayscale exports (26, 51, 77 ...) and ZRO's legal-range
    grayscale tools (16, 38, 60 ... 235).
    """
    return stimulus_pct_from_code_value(val)


def _nearest_target_pct(stim: float, targets: Tuple[float, ...]) -> Optional[float]:
    matches = [target for target in targets if abs(stim - target) <= PCT_SNAP_TOLERANCE]
    if not matches:
        return None
    return min(matches, key=lambda target: abs(stim - target))


def _xyY_to_XZ(x: float, y: float, Y: float) -> Tuple[float, float]:
    """Derive CIE X and Z from chromaticity + luminance."""
    if y == 0:
        return 0.0, 0.0
    X = (x / y) * Y
    Z = ((1.0 - x - y) / y) * Y
    return X, Z


def _to_measurement(row: _Row, label: str) -> dict:
    """Build a session-ready measurement dict from a parsed row."""
    X, Z = _xyY_to_XZ(row.x, row.y, row.Y)
    m = Measurement(
        x=row.x,
        y=row.y,
        Y=row.Y,
        X=X,
        Z=Z,
        timestamp=row.dt.isoformat(),
        label=label,
        stimulus_rgb=(row.r, row.g, row.b),
    )
    # Server stores measurements as plain dicts (via dataclasses.asdict)
    from dataclasses import asdict

    return asdict(m)


# ---------------------------------------------------------------------------
# Patch classifier
# ---------------------------------------------------------------------------


def _classify(r: int, g: int, b: int) -> str:
    """
    Return a patch type string for the given RGB stimulus.

    Returns one of:
        'white'    — R=G=B ≈ 235  (100% white)
        'black'    — R=G=B ≈ 16   (0% black)
        'grayscale'— R=G=B (other neutral)
        'red', 'green', 'blue', 'cyan', 'magenta', 'yellow'
        'unknown'
    """
    hi = max(r, g, b)
    if hi > 255:
        on_threshold = BIT10_ON_THRESHOLD
        off_threshold = BIT10_OFF_THRESHOLD
        white_threshold = BIT10_WHITE_THRESHOLD
        black_threshold = BIT10_BLACK_THRESHOLD
    else:
        on_threshold = CHANNEL_ON_THRESHOLD
        off_threshold = CHANNEL_OFF_THRESHOLD
        white_threshold = 235
        black_threshold = 16

    r_on = r >= on_threshold
    g_on = g >= on_threshold
    b_on = b >= on_threshold
    r_off = r <= off_threshold
    g_off = g <= off_threshold
    b_off = b <= off_threshold

    # Neutral (grayscale) — all channels within tolerance of each other
    if (
        abs(r - g) <= GRAYSCALE_EQUAL_TOLERANCE
        and abs(g - b) <= GRAYSCALE_EQUAL_TOLERANCE
    ):
        avg = (r + g + b) / 3
        if avg <= black_threshold + GRAYSCALE_EQUAL_TOLERANCE:
            return "black"
        if avg >= white_threshold - GRAYSCALE_EQUAL_TOLERANCE:
            return "white"
        return "grayscale"

    # Primaries
    if r_on and g_off and b_off:
        return "red"
    if r_off and g_on and b_off:
        return "green"
    if r_off and g_off and b_on:
        return "blue"

    # Secondaries
    if r_on and g_on and b_off:
        return "yellow"
    if r_off and g_on and b_on:
        return "cyan"
    if r_on and g_off and b_on:
        return "magenta"

    return "unknown"


# ---------------------------------------------------------------------------
# ABL / anomaly detection
# ---------------------------------------------------------------------------


def _check_abl(grayscale_rows: List[_Row]) -> List[str]:
    """
    Return warning strings for non-monotonic luminance in a grayscale ramp.

    A healthy grayscale ramp should be strictly increasing.  If a higher
    stimulus produces lower luminance, the TV's Auto Brightness Limiter (ABL)
    or dynamic tone-mapping is interfering with the measurement.
    """
    warnings: List[str] = []
    if len(grayscale_rows) < 3:
        return warnings

    sorted_rows = sorted(grayscale_rows, key=lambda r: r.r + r.g + r.b)
    for i in range(1, len(sorted_rows)):
        prev = sorted_rows[i - 1]
        curr = sorted_rows[i]
        prev_pct = _rgb_to_stimulus_pct(prev.r)  # r=g=b for grayscale
        curr_pct = _rgb_to_stimulus_pct(curr.r)
        if curr.Y < prev.Y * 0.95:  # >5% drop = real anomaly
            warnings.append(
                f"ABL/non-monotonic: {curr_pct:.0f}% stimulus → {curr.Y:.2f} nits "
                f"(lower than {prev_pct:.0f}% → {prev.Y:.2f} nits). "
                f"Auto Brightness Limiter may be active. Disable dynamic "
                f"contrast / local dimming before calibrating."
            )
    return warnings


# ---------------------------------------------------------------------------
# Session grouping
# ---------------------------------------------------------------------------


def _group_into_sessions(rows: List[_Row]) -> List[List[_Row]]:
    """
    Split rows into contiguous groups separated by SESSION_BREAK_SECONDS gaps.
    """
    if not rows:
        return []
    groups: List[List[_Row]] = [[rows[0]]]
    for row in rows[1:]:
        gap = (row.dt - groups[-1][-1].dt).total_seconds()
        if gap >= SESSION_BREAK_SECONDS:
            groups.append([])
        groups[-1].append(row)
    return groups


def _session_type(group: List[_Row]) -> str:
    """Infer whether a session group is 'colour', 'grayscale', or 'unknown'.

    'unknown' covers groups where no row matches any recognized colour or
    grayscale patch signature (e.g. a trailing block of intermediate-
    saturation CMS check patches) — these must not be treated as a real
    grayscale pass, or they'd silently displace the genuine post-cal ramp.
    """
    types = [_classify(r.r, r.g, r.b) for r in group]
    colour_count = sum(
        1 for t in types if t in {"red", "green", "blue", "cyan", "magenta", "yellow"}
    )
    gray_count = sum(1 for t in types if t in {"grayscale", "black", "white"})
    if colour_count == 0 and gray_count == 0:
        return "unknown"
    if colour_count > gray_count:
        return "colour"
    return "grayscale"


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------


def parse_zro_csv(source: str | bytes | io.IOBase) -> ZROImportResult:
    """
    Parse a Colorspace ZRO export and return a :class:`ZROImportResult`.

    Parameters
    ----------
    source:
        File path (str), raw bytes, or an already-opened file-like object.

    Returns
    -------
    ZROImportResult
        Populated with classified :class:`Measurement` dicts ready to be
        merged into a session dict via ``session.update(result_as_dict())``.
    """
    result = ZROImportResult()

    # ── normalise input ──────────────────────────────────────────────────────
    if isinstance(source, bytes):
        raw = source.decode("utf-8-sig")
    elif isinstance(source, str):
        # Distinguish between a file path and raw CSV content.
        # CSV content always contains a newline; valid file paths never do.
        if "\n" in source or "\t" in source:
            raw = source
        else:
            with open(source, newline="", encoding="utf-8-sig") as fh:
                raw = fh.read()
    else:
        raw = source.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig")

    # ── detect delimiter (tab or comma) ──────────────────────────────────────
    first_line = raw.splitlines()[0] if raw.strip() else ""
    delimiter = "\t" if "\t" in first_line else ","

    # ── parse rows ───────────────────────────────────────────────────────────
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)

    # Parse rows positionally — ZRO column order is fixed:
    #   datetime | R | G | B | Y (nits) | x | y (chromaticity) | msec
    # Positional indexing avoids the Y/y case-collision that causes DictReader
    # to merge luminance Y and chromaticity y into a single "y" key.
    rows: List[_Row] = []
    # Keep track of total processed lines to detect if every single row failed parsing
    processed_rows = 0
    for raw_row in reader:
        processed_rows += 1
        try:
            # Normalize: csv.DictReader fills missing trailing fields with None;
            # rows with extra columns store extras under raw_row[None] as a list.
            vals = [v if isinstance(v, str) else "" for v in raw_row.values()]
            if len(vals) < 7 or not all(vals[i] for i in range(7)):
                result.unknown_rows += 1
                if len(result.row_errors) < 20:
                    result.row_errors.append(
                        f"Row {processed_rows}: insufficient or empty fields "
                        f"(got {len(vals)}, need 7)"
                    )
                continue
            dt = _parse_datetime(vals[0])
            r = int(vals[1])
            g = int(vals[2])
            b = int(vals[3])
            Y = float(vals[4])
            x = float(vals[5])
            y_chroma = float(vals[6])
        except (IndexError, ValueError, TypeError, AttributeError) as exc:
            result.unknown_rows += 1
            if len(result.row_errors) < 20:
                result.row_errors.append(
                    f"Row {processed_rows}: {exc}"
                )
            continue

        row = _Row(dt=dt, r=r, g=g, b=b, Y=Y, x=x, y=y_chroma)
        rows.append(row)
        result.raw_rows.append(
            {
                "timestamp": dt.isoformat(),
                "r": r,
                "g": g,
                "b": b,
                "Y": Y,
                "x": x,
                "y": y_chroma,
                "classification": _classify(r, g, b),
                "stimulus_pct": _rgb_to_stimulus_pct(r) if r == g == b else None,
            }
        )

    result.total_rows = len(rows)

    # If we had rows in the CSV but none were successfully parsed, surface a fatal error.
    if processed_rows > 0 and not rows:
        result.fatal_error = (
            f"Failed to parse any rows from the CSV ({processed_rows} total). "
            "This usually means the timestamp format is unsupported or the file "
            "structure is unexpected."
        )
        return result

    if not rows:
        return result

    # ── split into sessions ──────────────────────────────────────────────────
    groups = _group_into_sessions(rows)
    result.sessions_detected = len(groups)
    for g_rows in groups[1:]:
        result.session_breaks.append(g_rows[0].dt.isoformat())

    # ── classify each session group ──────────────────────────────────────────
    # We need to know the total number of grayscale passes before processing
    # so we can distinguish pre, mid, and post.
    grayscale_groups = [g for g in groups if _session_type(g) == "grayscale"]
    n_grayscale = len(grayscale_groups)
    result.grayscale_sessions = n_grayscale

    grayscale_group_index = 0
    for group in groups:
        stype = _session_type(group)

        if stype == "colour":
            result.colour_sessions += 1
            _process_colour_group(group, result)

        elif stype == "grayscale":  # includes mixed groups won by grayscale
            is_last = (grayscale_group_index == n_grayscale - 1)
            _process_grayscale_group(group, result, grayscale_group_index, is_last)
            grayscale_group_index += 1
            # The cumulative log may have colour (CMS) rows mixed into a group
            # that is dominated by grayscale rows from previous steps.  Always
            # extract any colour-type rows so they reach cms_measurements.
            _extract_colour_rows(group, result)

        else:  # "unknown" — no recognizable patch in this group; don't
            # consume a pre/mid/post pass slot, just count the rows as
            # unclassifiable.
            result.unknown_rows += len(group)

    return result


# ---------------------------------------------------------------------------
# Group processors
# ---------------------------------------------------------------------------

_COLOUR_LABELS = {
    "red": "Red",
    "green": "Green",
    "blue": "Blue",
    "cyan": "Cyan",
    "magenta": "Magenta",
    "yellow": "Yellow",
}


def _extract_colour_rows(group: List[_Row], result: ZROImportResult) -> None:
    """Add any primary/secondary colour rows from a group to cms_measurements.

    Called on groups classified as 'grayscale' to rescue colour patches that
    ended up outnumbered by grayscale rows in the cumulative log file.
    """
    for row in group:
        patch_type = _classify(row.r, row.g, row.b)
        label = _COLOUR_LABELS.get(patch_type)
        if label is not None:
            result.cms_measurements.append(_to_measurement(row, label))


def _process_colour_group(group: List[_Row], result: ZROImportResult) -> None:
    """Classify and store primary/secondary colour patches."""
    COLOUR_LABELS = {
        "red": "Red",
        "green": "Green",
        "blue": "Blue",
        "cyan": "Cyan",
        "magenta": "Magenta",
        "yellow": "Yellow",
        "white": "White (100%)",
    }
    for row in group:
        patch_type = _classify(row.r, row.g, row.b)
        label = COLOUR_LABELS.get(patch_type)
        if label is None:
            result.unknown_rows += 1
            continue
        m = _to_measurement(row, label)
        if patch_type == "white":
            result.lum_measurements.append(m)
        else:
            result.cms_measurements.append(m)


def _process_grayscale_group(
    group: List[_Row],
    result: ZROImportResult,
    pass_index: int,
    is_last: bool,
) -> None:
    """
    Classify grayscale ramp rows, run ABL detection, and populate buckets.

    pass_index == 0 → pre_measurements   (pre-calibration)
    is_last        → post_measurements  (post-calibration)
    Others         → mid_measurements   (diagnostic/mid-cal)
    Also extracts wb_measurements and gamma_measurements from the same rows.
    """
    gray_rows: List[_Row] = []
    for row in group:
        patch_type = _classify(row.r, row.g, row.b)
        if patch_type == "unknown":
            result.unknown_rows += 1
            continue
        if patch_type not in {"grayscale", "black", "white"}:
            continue
        gray_rows.append(row)

    # ABL check (always run, regardless of pass)
    pass_warnings = _check_abl(gray_rows)
    result.abl_warnings.extend(pass_warnings)

    # Populate the primary grayscale bucket
    if pass_index == 0:
        target_list = result.pre_measurements
    elif is_last and pass_index > 0:
        target_list = result.post_measurements
    else:
        target_list = result.mid_measurements
    pass_measurements: List[dict] = []

    for row in gray_rows:
        stim = _rgb_to_stimulus_pct(row.r)  # r=g=b for neutral rows
        label = _grayscale_label(stim)
        m = _to_measurement(row, label)
        target_list.append(m)
        pass_measurements.append(m)

        # Also populate specialised buckets where this step is relevant
        # ── luminance reference: 100% white ──
        if stim >= 99.0:
            result.lum_measurements.append(m)

        # ── white balance: 80% gain, 30% offset ──
        if _nearest_target_pct(stim, (WB_GAIN_TARGET_PCT,)) is not None:
            result.wb_measurements.append(
                _to_measurement(row, f"WB Gain ({stim:.0f}% gray)")
            )
        elif _nearest_target_pct(stim, (WB_OFFSET_TARGET_PCT,)) is not None:
            result.wb_measurements.append(
                _to_measurement(row, f"WB Offset ({stim:.0f}% gray)")
            )

        # ── gamma tracking: keep any snapped 5% gray points for quick or fine passes ──
        target_pct = _nearest_target_pct(stim, GAMMA_TARGET_PCTS)
        if target_pct is not None:
            result.gamma_measurements.append(
                _to_measurement(row, f"Gamma {target_pct:.0f}%")
            )

    result.grayscale_passes.append(pass_measurements)
    result.grayscale_pass_warnings.append(pass_warnings)


def _grayscale_label(stim_pct: float) -> str:
    """Human-readable label for a grayscale stimulus level."""
    if stim_pct <= 0.5:
        return "Black (0%)"
    if stim_pct >= 99.5:
        return "White (100%)"
    return f"{stim_pct:.0f}% Gray"


# ---------------------------------------------------------------------------
# Convenience helper for the API layer
# ---------------------------------------------------------------------------


def _measurement_key(m: Dict[str, Any]) -> str:
    """Generate a unique key for a measurement to detect duplicates.

    Handles both plain dicts (as stored in sessions) and
    :class:`calcore.models.Measurement` dataclass instances.
    """
    if isinstance(m, dict):
        ts = m.get("timestamp", "")
        stimulus_rgb = m.get("stimulus_rgb", (0, 0, 0))
    else:
        ts = getattr(m, "timestamp", "")
        stimulus_rgb = getattr(m, "stimulus_rgb", (0, 0, 0))
    r = stimulus_rgb[0] if stimulus_rgb else 0
    g = stimulus_rgb[1] if stimulus_rgb else 0
    b = stimulus_rgb[2] if stimulus_rgb else 0
    return f"{ts}:{r}:{g}:{b}"


def merge_into_session(session: dict, result: ZROImportResult) -> dict:
    """
    Merge a :class:`ZROImportResult` into an existing session dict in-place.

    Existing measurements are preserved; imported ones are appended.
    Duplicate measurements (matching timestamp and stimulus RGB) within the same
    bucket are skipped, but the same physical reading can coexist across different
    buckets (e.g., a grayscale row that belongs in pre_measurements, wb_measurements,
    and gamma_measurements simultaneously).

    Returns the modified session dict.

    Raises:
        ValueError: If result contains a fatal_error from parsing.
    """
    if result.fatal_error:
        raise ValueError(result.fatal_error)

    bucket_map = {
        "cms_measurements": result.cms_measurements,
        "pre_measurements": result.pre_measurements,
        "mid_measurements": result.mid_measurements,
        "post_measurements": result.post_measurements,
        "lum_measurements": result.lum_measurements,
        "wb_measurements": result.wb_measurements,
        "gamma_measurements": result.gamma_measurements,
    }

    duplicates_detected = 0

    for key, measurements in bucket_map.items():
        if not measurements:
            continue

        # Per-bucket deduplication set (seeded from session state for this bucket)
        existing_keys: Set[str] = set()
        for m in session.get(key, []):
            existing_keys.add(_measurement_key(m))

        existing = session.setdefault(key, [])
        for m in measurements:
            m_key = _measurement_key(m)
            if m_key in existing_keys:
                duplicates_detected += 1
                continue
            existing_keys.add(m_key)
            existing.append(m)

    import_meta = {
        "total_rows": result.total_rows,
        "sessions_detected": result.sessions_detected,
        "colour_sessions": result.colour_sessions,
        "grayscale_sessions": result.grayscale_sessions,
        "session_breaks": result.session_breaks,
        "abl_warnings": result.abl_warnings,
        "unknown_rows": result.unknown_rows,
        "duplicates_skipped": duplicates_detected,
        "buckets_populated": {k: len(v) for k, v in bucket_map.items() if v},
    }

    # Surface mid-calibration ramps in metadata for diagnostic access.
    if result.grayscale_sessions > 2:
        import_meta["mid_passes"] = result.grayscale_passes[1:-1]
    else:
        import_meta["mid_passes"] = []
    session.setdefault("zro_imports", []).append(import_meta)
    return session
