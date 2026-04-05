"""Shared calibrator dataclasses built on calcore models."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
from typing import List

from calcore import CalMode, CalibrationTarget, Measurement

from .utils import delta_e_ciede2000_xyY, stimulus_pct_from_code_value


@dataclass
class CalibrationReport:
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
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Phase", "Level%", "x", "y", "Y_nits", "CCT", "dE"])

            for phase, measurements in (
                ("Pre-Cal", self.pre_cal_grayscale),
                ("Post-Cal", self.post_cal_grayscale),
            ):
                for m in measurements:
                    stim_pct = stimulus_pct_from_code_value(m.stimulus_rgb[0])
                    de = delta_e_ciede2000_xyY(
                        m.x,
                        m.y,
                        m.Y,
                        self.target.white_point_xy[0],
                        self.target.white_point_xy[1],
                        self.target.peak_luminance_nits * (stim_pct / 100) ** self.target.gamma
                        if self.target.gamma > 0 else m.Y,
                    )
                    writer.writerow([phase, stim_pct, m.x, m.y, m.Y, round(m.cct, 0), round(de, 2)])

    def save_html(self, filepath: str):
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
