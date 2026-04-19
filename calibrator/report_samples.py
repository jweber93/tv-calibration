"""Deterministic sample calibration report fixtures for tests and CI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from calcore.models import Measurement

from .reports import render_report_html, render_report_pdf, report_payload
from .session import SDR_TARGET


def _measurement(
    label: str,
    stimulus_pct: int,
    y_value: float,
    *,
    x: float = 0.3127,
    y: float = 0.3290,
    x_scale: float = 0.95,
    z_scale: float = 1.09,
) -> Measurement:
    stimulus = round(stimulus_pct * 255 / 100)
    return Measurement(
        x=x,
        y=y,
        Y=y_value,
        X=y_value * x_scale,
        Z=y_value * z_scale,
        label=label,
        stimulus_rgb=(stimulus, stimulus, stimulus),
    )


def build_sample_report() -> Dict[str, Any]:
    """Build a stable sample report payload for visual regression tests."""
    steps = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    pre_measurements = [
        _measurement(f"{step}% Gray", step, float(step) * 1.01)
        for step in steps
    ]
    post_measurements = [
        _measurement(
            f"{step}% Gray",
            step,
            float(step) * 1.03,
            x=0.3123,
            y=0.3287,
            x_scale=0.97,
            z_scale=1.06,
        )
        for step in steps
    ]
    wb_measurements = [
        _measurement("30% Gray", 30, 31.4, x=0.3124, y=0.3288),
        _measurement("80% Gray", 80, 82.1, x=0.3128, y=0.3292),
    ]
    gamma_measurements = [
        _measurement("10% Gray", 10, 2.6, x=0.3125, y=0.3289),
        _measurement("25% Gray", 25, 9.7, x=0.3125, y=0.3290),
        _measurement("50% Gray", 50, 27.4, x=0.3126, y=0.3290),
        _measurement("75% Gray", 75, 58.9, x=0.3127, y=0.3291),
    ]

    session = {
        "tv_name": "Hisense U8G",
        "mode": "SDR",
        "created_at": "2026-04-19T09:30:00Z",
        "peak_luminance": 514.2,
        "target": SDR_TARGET,
        "signal_range": "full",
        "code_scale": "8bit",
        "pre_measurements": pre_measurements,
        "post_measurements": post_measurements,
        "wb_measurements": wb_measurements,
        "cms_measurements": [],
        "gamma_measurements": gamma_measurements,
        "repass_count": 1,
        "repass_decision": {
            "reason": "Post-cal average delta E exceeded the comfort threshold.",
            "action": "Recommend a final white balance touch-up pass.",
        },
    }
    return report_payload(session)


def write_sample_report_artifacts(
    output_dir: str | Path,
    *,
    include_pdf: bool = True,
) -> Dict[str, Path]:
    """Write sample report artifacts into output_dir."""
    report = build_sample_report()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "sample-calibration-report.json"
    html_path = out_dir / "sample-calibration-report.html"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    html_path.write_text(render_report_html(report), encoding="utf-8")

    artifacts = {
        "json": json_path,
        "html": html_path,
    }
    if include_pdf:
        pdf_path = out_dir / "sample-calibration-report.pdf"
        pdf_path.write_bytes(render_report_pdf(report))
        artifacts["pdf"] = pdf_path

    return artifacts
