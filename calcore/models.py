from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Tuple

from .colour import D65_XY
from .spaces import BT709_PRIMARIES, BT2020_PRIMARIES, detect_primaries


@dataclass
class Patch:
    label: str
    r_target: int
    g_target: int
    b_target: int
    meas_xyz: Tuple[float, float, float]
    meas_yxy: Optional[Tuple[float, float, float]] = None  # (Y, x, y)
    kind: str = "color"

    @property
    def is_grayscale(self) -> bool:
        return self.kind == "grayscale"

    @property
    def sat_bucket(self) -> str:
        if self.is_grayscale:
            return "gray"
        mx = max(self.r_target, self.g_target, self.b_target)
        if mx >= 1000:
            return "100"
        if 720 <= mx <= 820:
            return "75"
        return "other"


@dataclass
class AnalysisConfig:
    mode: str = "hdr"  # sdr or hdr
    eotf: str = "pq"  # pq, gamma22, bt1886, or numeric gamma
    target_space: str = "p3d65"  # bt709, p3d65, bt2020
    code_max: int = 1023


@dataclass
class LLMConfig:
    endpoint: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.2
    timeout: float = 120.0


@dataclass
class Summary:
    grayscale_avg_de: Optional[float]
    grayscale_max_de: Optional[float]
    grayscale_over_3: int
    gamma_midtones: Optional[float]
    pq_err_midtones: Optional[float]
    color_75_avg_de: Optional[float]
    color_75_max_de: Optional[float]
    color_75_chroma_avg: Optional[float]
    color_100_avg_de: Optional[float]
    color_100_max_de: Optional[float]
    color_100_chroma_avg: Optional[float]
    grayscale_rows: List[Dict[str, Any]]
    color_rows: List[Dict[str, Any]]
    meta: Dict[str, Any]


@dataclass
class SessionState:
    phase: str = "baseline"
    last_mtime: float = 0.0
    last_report_hash: str = ""
    config: AnalysisConfig = field(default_factory=AnalysisConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)


class CalMode(Enum):
    SDR = "SDR"
    HDR10 = "HDR10"
    DOLBY_VISION = "Dolby Vision"


_ROBERTSON_TABLE = [
    (0, 0.18006, 0.26352, -0.24341),
    (10, 0.18066, 0.26589, -0.25479),
    (20, 0.18133, 0.26846, -0.26876),
    (30, 0.18208, 0.27119, -0.28539),
    (40, 0.18293, 0.27407, -0.30470),
    (50, 0.18388, 0.27709, -0.32675),
    (60, 0.18494, 0.28021, -0.35156),
    (70, 0.18611, 0.28342, -0.37915),
    (80, 0.18740, 0.28668, -0.40955),
    (90, 0.18880, 0.28997, -0.44278),
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
    last_d = 0.0
    last_entry = _ROBERTSON_TABLE[0]
    for entry in _ROBERTSON_TABLE[1:]:
        _r, ur, vr, t = entry
        d = ((v - vr) - t * (u - ur)) / math.sqrt(1.0 + t * t)
        if d * last_d < 0:
            _r_prev, u_prev, v_prev, _ = last_entry
            f = last_d / (last_d - d)
            u_bb = u_prev + f * (ur - u_prev)
            v_bb = v_prev + f * (vr - v_prev)
            duv = math.sqrt((u - u_bb) ** 2 + (v - v_bb) ** 2)
            return duv if v >= v_bb else -duv
        last_d = d
        last_entry = entry
    return 0.0


@dataclass
class Measurement:
    x: float = 0.0
    y: float = 0.0
    Y: float = 0.0
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
        n = (self.x - 0.3320) / (0.1858 - self.y) if self.y != 0.1858 else 0
        return 449 * n**3 + 3525 * n**2 + 6823.3 * n + 5520.33

    @property
    def delta_uv(self) -> float:
        denom = -2 * self.x + 12 * self.y + 3
        if denom == 0 or (self.x + self.y) <= 0:
            return 0.0
        u = 4 * self.x / denom
        v = 6 * self.y / denom
        return _robertson_duv(u, v)


@dataclass
class CalibrationTarget:
    mode: CalMode
    white_point_xy: Tuple[float, float] = D65_XY
    gamma: float = 2.2
    peak_luminance_nits: float = 100.0
    gamut: str = "bt709"
    primaries: Dict[str, Tuple[float, float]] = field(
        default_factory=lambda: dict(BT709_PRIMARIES)
    )
    eotf: str = "BT.1886"

    def __post_init__(self) -> None:
        if not self.primaries:
            self.primaries = detect_primaries(self.gamut)
        # Normalize display names that older calibrator code expects.
        if self.gamut.lower() in ("bt709", "709", "rec709"):
            self.gamut = "Rec.709"
        elif self.gamut.lower() in ("p3d65", "displayp3", "p3"):
            self.gamut = "P3 D65"
        elif self.gamut.lower() in ("bt2020", "2020", "rec2020"):
            self.gamut = "Rec.2020"


SDR_TARGET = CalibrationTarget(
    mode=CalMode.SDR,
    gamma=2.2,
    peak_luminance_nits=120.0,
    gamut="bt709",
    eotf="BT.1886",
)

HDR10_TARGET = CalibrationTarget(
    mode=CalMode.HDR10,
    gamma=0,
    peak_luminance_nits=1000.0,
    gamut="bt2020",
    primaries=dict(BT2020_PRIMARIES),
    eotf="PQ (ST.2084)",
)

DV_TARGET = CalibrationTarget(
    mode=CalMode.DOLBY_VISION,
    gamma=0,
    peak_luminance_nits=1000.0,
    gamut="bt2020",
    primaries=dict(BT2020_PRIMARIES),
    eotf="PQ (ST.2084)",
)
