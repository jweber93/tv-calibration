"""
Colour science primitives, shared dataclasses, and pre-built calibration targets.

This module has no dependencies on other calibrator submodules and can be
imported standalone.
"""

import csv
import importlib
import json
import math
from html import escape
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Dependency bootstrap
# ---------------------------------------------------------------------------

REQUIRED_PACKAGES = ["rich", "numpy"]


def ensure_packages():
    """Raise a clear error if required runtime packages are missing."""
    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        missing_list = ", ".join(missing)
        raise ImportError(
            f"Missing required packages: {missing_list}. "
            "Install dependencies with `pip install -r requirements.txt`."
        )

from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Colour science constants
# ---------------------------------------------------------------------------

# CIE 1931 2-degree observer reference white D65
D65_XY = (0.3127, 0.3290)
D65_XYZ = (0.95047, 1.0, 1.08883)

# Rec.709 / sRGB primaries (CIE xy)
REC709_PRIMARIES = {
    "red":   (0.640, 0.330),
    "green": (0.300, 0.600),
    "blue":  (0.150, 0.060),
}

# DCI-P3 primaries
P3_PRIMARIES = {
    "red":   (0.680, 0.320),
    "green": (0.265, 0.690),
    "blue":  (0.150, 0.060),
}

# Rec.2020 primaries
REC2020_PRIMARIES = {
    "red":   (0.708, 0.292),
    "green": (0.170, 0.797),
    "blue":  (0.131, 0.046),
}

# Standard SDR grayscale stimulus levels (percentage)
GRAYSCALE_LEVELS = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50,
                    55, 60, 65, 70, 75, 80, 85, 90, 95, 100]

# Standard saturation sweep levels
SATURATION_LEVELS = [25, 50, 75, 100]

# Primary + secondary colours for CMS check
COLOUR_TARGETS = ["red", "green", "blue", "cyan", "magenta", "yellow"]

# Gamma targets
GAMMA_SDR = 2.2   # BT.1886 approximate
GAMMA_HDR_PQ = "PQ"  # ST.2084


# ---------------------------------------------------------------------------
# Enums and dataclasses
# ---------------------------------------------------------------------------

class CalMode(Enum):
    SDR = "SDR"
    HDR10 = "HDR10"
    DOLBY_VISION = "Dolby Vision"


@dataclass
class Measurement:
    """Single colour measurement from the meter."""
    x: float = 0.0
    y: float = 0.0
    Y: float = 0.0  # luminance in cd/m² (nits)
    X: float = 0.0
    Z: float = 0.0
    timestamp: str = ""
    label: str = ""
    stimulus_rgb: Tuple[int, int, int] = (0, 0, 0)

    @property
    def xy(self) -> Tuple[float, float]:
        return (self.x, self.y)

    @property
    def cct(self) -> float:
        """Approximate correlated colour temperature (McCamy)."""
        n = (self.x - 0.3320) / (0.1858 - self.y) if self.y != 0.1858 else 0
        return 449 * n**3 + 3525 * n**2 + 6823.3 * n + 5520.33

    @property
    def delta_uv(self) -> float:
        """Signed perpendicular distance from the Planckian locus in CIE 1960 UCS.

        Positive = above locus (greenish), negative = below locus (magenta-ish).
        Uses Robertson (1968) isotemperature line interpolation.
        """
        denom = -2 * self.x + 12 * self.y + 3
        if denom == 0 or (self.x + self.y) <= 0:
            return 0.0
        u = 4 * self.x / denom
        v = 6 * self.y / denom
        return _robertson_duv(u, v)


@dataclass
class CalibrationTarget:
    """Target values for a given calibration mode."""
    mode: CalMode
    white_point_xy: Tuple[float, float] = D65_XY
    gamma: float = 2.2  # or "PQ" for HDR
    peak_luminance_nits: float = 100.0
    gamut: str = "Rec.709"
    primaries: Dict = field(default_factory=lambda: dict(REC709_PRIMARIES))
    eotf: str = "BT.1886"


# Pre-built targets
SDR_TARGET = CalibrationTarget(
    mode=CalMode.SDR, gamma=2.2, peak_luminance_nits=120.0,
    gamut="Rec.709", eotf="BT.1886"
)
HDR10_TARGET = CalibrationTarget(
    mode=CalMode.HDR10, gamma=0, peak_luminance_nits=1000.0,
    gamut="Rec.2020", primaries=dict(REC2020_PRIMARIES), eotf="PQ (ST.2084)"
)
DV_TARGET = CalibrationTarget(
    mode=CalMode.DOLBY_VISION, gamma=0, peak_luminance_nits=1000.0,
    gamut="Rec.2020", primaries=dict(REC2020_PRIMARIES), eotf="PQ (ST.2084)"
)


# ---------------------------------------------------------------------------
# Colour math utilities
# ---------------------------------------------------------------------------

# Robertson (1968) isotemperature line table: (reciprocal megakelvin, u, v, slope t)
_ROBERTSON_TABLE = [
    (  0, 0.18006, 0.26352, -0.24341),
    ( 10, 0.18066, 0.26589, -0.25479),
    ( 20, 0.18133, 0.26846, -0.26876),
    ( 30, 0.18208, 0.27119, -0.28539),
    ( 40, 0.18293, 0.27407, -0.30470),
    ( 50, 0.18388, 0.27709, -0.32675),
    ( 60, 0.18494, 0.28021, -0.35156),
    ( 70, 0.18611, 0.28342, -0.37915),
    ( 80, 0.18740, 0.28668, -0.40955),
    ( 90, 0.18880, 0.28997, -0.44278),
    (100, 0.19032, 0.29326, -0.47888),
    (125, 0.19462, 0.30141, -0.58204),
    (150, 0.19962, 0.30921, -0.70471),
    (175, 0.20525, 0.31647, -0.84901),
    (200, 0.21142, 0.32312, -1.01820),
    (225, 0.21807, 0.32909, -1.21680),
    (250, 0.22511, 0.33439, -1.45120),
    (275, 0.23247, 0.33904, -1.72980),
    (300, 0.24010, 0.34308, -2.06370),
    (325, 0.24792, 0.34655, -2.46810),
    (350, 0.25591, 0.34951, -2.96410),
    (375, 0.26400, 0.35200, -3.58140),
    (400, 0.27218, 0.35407, -4.36330),
    (425, 0.28039, 0.35577, -5.37620),
    (450, 0.28863, 0.35714, -6.72620),
    (475, 0.29685, 0.35823, -8.59550),
    (500, 0.30505, 0.35907, -11.3240),
    (525, 0.31320, 0.35968, -15.6280),
    (550, 0.32129, 0.36011, -23.3250),
    (575, 0.32931, 0.36038, -40.7700),
    (600, 0.33724, 0.36051, -116.450),
]


def _robertson_duv(u: float, v: float) -> float:
    """Signed perpendicular distance from Planckian locus using Robertson (1968).

    Positive = above locus (greenish), negative = below (magenta-ish).
    Returns 0.0 if the point falls outside the table range.
    """
    last_d = 0.0
    last_entry = _ROBERTSON_TABLE[0]
    for entry in _ROBERTSON_TABLE[1:]:
        r, ur, vr, t = entry
        d = ((v - vr) - t * (u - ur)) / math.sqrt(1.0 + t * t)
        if d * last_d < 0:
            # Bracketed: interpolate between last_entry and entry
            r_prev, u_prev, v_prev, _ = last_entry
            f = last_d / (last_d - d)
            u_bb = u_prev + f * (ur - u_prev)
            v_bb = v_prev + f * (vr - v_prev)
            duv = math.sqrt((u - u_bb) ** 2 + (v - v_bb) ** 2)
            return duv if v >= v_bb else -duv
        last_d = d
        last_entry = entry
    return 0.0


def delta_e_cie76(x1, y1, Y1, x2, y2, Y2) -> float:
    """CIE76 delta-E in CIELAB between two xyY measurements."""
    lab1 = xyY_to_lab(x1, y1, Y1)
    lab2 = xyY_to_lab(x2, y2, Y2)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(lab1, lab2)))


def xyY_to_XYZ(x, y, Y_lum) -> Tuple[float, float, float]:
    if y == 0:
        return (0, 0, 0)
    X = (x / y) * Y_lum
    Z = ((1 - x - y) / y) * Y_lum
    return (X, Y_lum, Z)


def XYZ_to_lab(X, Y, Z, ref_white=D65_XYZ) -> Tuple[float, float, float]:
    """Convert CIE XYZ to CIELAB."""
    def f(t):
        delta = 6 / 29
        if t > delta ** 3:
            return t ** (1 / 3)
        return t / (3 * delta ** 2) + 4 / 29

    xr, yr, zr = X / ref_white[0], Y / ref_white[1], Z / ref_white[2]
    L = 116 * f(yr) - 16
    a = 500 * (f(xr) - f(yr))
    b = 200 * (f(yr) - f(zr))
    return (L, a, b)


def xyY_to_lab(x, y, Y_nits, ref_nits=100.0) -> Tuple[float, float, float]:
    """Convert xyY (with luminance in nits) to CIELAB, normalised to ref_nits."""
    Y_norm = Y_nits / ref_nits if ref_nits > 0 else Y_nits
    X, Y_val, Z = xyY_to_XYZ(x, y, Y_norm)
    return XYZ_to_lab(X, Y_val, Z)


def delta_xy(measured_xy, target_xy) -> float:
    """Euclidean distance in CIE xy space."""
    return math.sqrt((measured_xy[0] - target_xy[0])**2 +
                     (measured_xy[1] - target_xy[1])**2)


def stimulus_pct_from_code_value(value: int, signal_range: str = "auto") -> float:
    """
    Convert a ZRO grayscale code value to stimulus percent.

    signal_range:
      "full"    — Full Range (0=black, 255=white): value / 255 * 100
      "limited" — Limited Range (16=black, 235=white): (value-16) / 219 * 100
      "full10"  — Full Range 10-bit (0=black, 1023=white): value / 1023 * 100
      "auto"    — Snap to whichever scale lands closer to the 5%-grid (legacy
                  behaviour for CSV imports where the range is unknown).
    """
    if signal_range == "full10":
        return round(min(100.0, max(0.0, value / 1023.0 * 100.0)), 1)
    if signal_range == "full":
        return round(min(100.0, max(0.0, value / 255.0 * 100.0)), 1)
    if signal_range == "limited":
        if value <= 16:
            return 0.0
        if value >= 235:
            return 100.0
        return round((value - 16) / (235 - 16) * 100.0, 1)
    # "auto" — snap to whichever scale is closer to the expected 5% grid
    if value > 255:
        return round(min(100.0, max(0.0, value / 1023.0 * 100.0)), 1)
    if value <= 16:
        return 0.0
    if value >= 235:
        return 100.0
    full_range_pct = value / 255.0 * 100.0
    legal_range_pct = (value - 16) / (235 - 16) * 100.0

    snap_levels = range(0, 101, 5)

    def snap_error(pct: float) -> float:
        return min(abs(pct - level) for level in snap_levels)

    chosen_pct = legal_range_pct if snap_error(legal_range_pct) < snap_error(full_range_pct) else full_range_pct
    return round(chosen_pct, 1)


def gamma_from_luminance(measured_nits, peak_nits, stimulus_pct) -> Optional[float]:
    """Calculate effective gamma from a single grayscale point."""
    if stimulus_pct <= 0 or stimulus_pct >= 100 or peak_nits <= 0:
        return None
    normalised_out = measured_nits / peak_nits
    normalised_in = stimulus_pct / 100.0
    if normalised_out <= 0 or normalised_in <= 0 or normalised_out > 1.0:
        return None
    return math.log(normalised_out) / math.log(normalised_in)


def rating_emoji(delta_e: float) -> str:
    """Return a quality rating based on delta-E."""
    if delta_e <= 1.0:
        return "[bold green]★★★ Excellent[/bold green]"
    elif delta_e <= 2.0:
        return "[green]★★☆ Good[/green]"
    elif delta_e <= 3.0:
        return "[yellow]★☆☆ Acceptable[/yellow]"
    else:
        return "[red]✗ Needs work[/red]"


def direction_hint(measured_val, target_val, label="") -> str:
    """Suggest which direction to adjust."""
    diff = measured_val - target_val
    if abs(diff) < 0.001:
        return "[green]On target[/green]"
    elif diff > 0:
        return f"[yellow]↓ Decrease {label}[/yellow]"
    else:
        return f"[cyan]↑ Increase {label}[/cyan]"


# ---------------------------------------------------------------------------
# Calibration Report
# ---------------------------------------------------------------------------

@dataclass
class CalibrationReport:
    """Stores all measurements for a calibration session."""
    mode: CalMode
    target: CalibrationTarget
    tv_model: str = "Unknown TV"
    meter: str = "Calibrite ColorChecker Display Plus"
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))

    pre_cal_grayscale: List[Measurement] = field(default_factory=list)
    post_cal_grayscale: List[Measurement] = field(default_factory=list)
    pre_cal_primaries: List[Measurement] = field(default_factory=list)
    post_cal_primaries: List[Measurement] = field(default_factory=list)

    pre_cal_avg_de: float = 0.0
    post_cal_avg_de: float = 0.0
    pre_cal_max_de: float = 0.0
    post_cal_max_de: float = 0.0
    peak_luminance: float = 0.0
    black_level: float = 0.0

    def save_json(self, filepath: str):
        """Save report as JSON."""
        data = {
            "tv_model": self.tv_model,
            "meter": self.meter,
            "date": self.date,
            "mode": self.mode.value,
            "target_gamut": self.target.gamut,
            "target_eotf": self.target.eotf,
            "target_peak_nits": self.target.peak_luminance_nits,
            "pre_cal_avg_dE": self.pre_cal_avg_de,
            "post_cal_avg_dE": self.post_cal_avg_de,
            "pre_cal_max_dE": self.pre_cal_max_de,
            "post_cal_max_dE": self.post_cal_max_de,
            "peak_luminance_nits": self.peak_luminance,
            "black_level_nits": self.black_level,
            "pre_cal_grayscale": [asdict(m) for m in self.pre_cal_grayscale],
            "post_cal_grayscale": [asdict(m) for m in self.post_cal_grayscale],
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def save_csv(self, filepath: str):
        """Save grayscale measurements as CSV."""
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Phase", "Level%", "x", "y", "Y_nits", "CCT", "dE"])

            for m in self.pre_cal_grayscale:
                stim_pct = stimulus_pct_from_code_value(m.stimulus_rgb[0])
                de = delta_e_cie76(
                    m.x, m.y, m.Y,
                    self.target.white_point_xy[0],
                    self.target.white_point_xy[1],
                    self.target.peak_luminance_nits * (stim_pct / 100) ** self.target.gamma
                    if self.target.gamma > 0 else m.Y,
                )
                writer.writerow(["Pre-Cal", stim_pct, m.x, m.y, m.Y,
                                  round(m.cct, 0), round(de, 2)])

            for m in self.post_cal_grayscale:
                stim_pct = stimulus_pct_from_code_value(m.stimulus_rgb[0])
                de = delta_e_cie76(
                    m.x, m.y, m.Y,
                    self.target.white_point_xy[0],
                    self.target.white_point_xy[1],
                    self.target.peak_luminance_nits * (stim_pct / 100) ** self.target.gamma
                    if self.target.gamma > 0 else m.Y,
                )
                writer.writerow(["Post-Cal", stim_pct, m.x, m.y, m.Y,
                                  round(m.cct, 0), round(de, 2)])

    def save_html(self, filepath: str):
        """Save a readable HTML summary report."""
        improvement = None
        if self.pre_cal_avg_de and self.post_cal_avg_de and self.pre_cal_avg_de > 0:
            improvement = round((1 - self.post_cal_avg_de / self.pre_cal_avg_de) * 100, 1)

        def rows(measurements: List[Measurement]) -> str:
            if not measurements:
                return '<tr><td colspan="4">No measurements recorded</td></tr>'
            out = []
            for m in measurements:
                out.append(
                    "<tr>"
                    f"<td>{escape(m.label or '')}</td>"
                    f"<td>{m.Y:.1f}</td>"
                    f"<td>{m.x:.4f}</td>"
                    f"<td>{m.y:.4f}</td>"
                    "</tr>"
                )
            return "".join(out)

        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(self.tv_model)} Calibration Report</title>
  <style>
    body {{ margin: 0; font-family: Georgia, "Times New Roman", serif; background: #f7f4ed; color: #201b17; }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 32px 20px 48px; }}
    .hero, .card {{ background: #fffdf8; border: 1px solid #d8cfbf; border-radius: 16px; padding: 20px; }}
    .hero {{ margin-bottom: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 18px; }}
    .label {{ color: #6f6558; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 8px; }}
    .value {{ font-size: 28px; font-weight: 700; }}
    h1 {{ margin: 0 0 8px; }}
    h2 {{ margin: 24px 0 12px; }}
    .sub {{ color: #6f6558; }}
    table {{ width: 100%; border-collapse: collapse; background: #fffdf8; border: 1px solid #d8cfbf; border-radius: 14px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #d8cfbf; }}
    th {{ background: #f3ede3; color: #6f6558; }}
    tr:last-child td {{ border-bottom: none; }}
    .two-col {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="label">Calibration Summary</div>
      <h1>{escape(self.tv_model)}</h1>
      <div class="sub">{escape(self.mode.value)} · {escape(self.date)} · Target {escape(self.target.gamut)} / {escape(self.target.eotf)}</div>
    </section>
    <section class="grid">
      <div class="card"><div class="label">Pre-Cal Avg ΔE</div><div class="value">{self.pre_cal_avg_de:.2f}</div></div>
      <div class="card"><div class="label">Post-Cal Avg ΔE</div><div class="value">{self.post_cal_avg_de:.2f}</div></div>
      <div class="card"><div class="label">Peak Luminance</div><div class="value">{self.peak_luminance:.1f} nits</div></div>
      <div class="card"><div class="label">Improvement</div><div class="value">{'—' if improvement is None else f'{improvement:.1f}%'}</div></div>
    </section>
    <section class="two-col">
      <div>
        <h2>Pre-Calibration Grayscale</h2>
        <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead><tbody>{rows(self.pre_cal_grayscale)}</tbody></table>
      </div>
      <div>
        <h2>Post-Calibration Grayscale</h2>
        <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead><tbody>{rows(self.post_cal_grayscale)}</tbody></table>
      </div>
    </section>
  </div>
</body>
</html>"""

        with open(filepath, "w") as f:
            f.write(html)
