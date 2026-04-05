from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


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
