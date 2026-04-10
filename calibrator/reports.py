"""Report payload and HTML rendering extracted from server.py."""

from __future__ import annotations

from html import escape
from typing import Any, Dict, List

from fastapi import HTTPException

from .session import m_to_dict


def report_payload(session: Dict[str, Any]) -> Dict[str, Any]:
    target = session.get("target")
    if not target:
        raise HTTPException(400, "No calibration mode selected")
    signal_range = session.get("signal_range", "limited")
    code_scale = session.get("code_scale", "8bit")

    def stats(measurements: list) -> Dict[str, Any]:
        if not measurements:
            return {"avg_de": None, "max_de": None, "measurements": []}
        items = [m_to_dict(m, target, signal_range, code_scale) for m in measurements]
        des = [item["delta_e"] for item in items]
        return {
            "avg_de": round(sum(des) / len(des), 2),
            "max_de": round(max(des), 2),
            "measurements": items,
        }

    def gamma_stats(measurements: list) -> Dict[str, Any]:
        items = [m_to_dict(m, target, signal_range, code_scale) for m in measurements]
        gammas = [item["effective_gamma"] for item in items if item.get("effective_gamma") is not None]
        return {
            "avg_gamma": round(sum(gammas) / len(gammas), 3) if gammas else None,
            "measurements": items,
        }

    pre = stats(session["pre_measurements"])
    post = stats(session["post_measurements"])
    wb = stats(session["wb_measurements"])
    cms = stats(session["cms_measurements"])
    gamma = gamma_stats(session["gamma_measurements"])
    improvement = None
    if pre["avg_de"] and post["avg_de"] and pre["avg_de"] > 0:
        improvement = round((1 - post["avg_de"] / pre["avg_de"]) * 100, 1)

    return {
        "tv": session["tv_name"],
        "mode": session["mode"],
        "date": session["created_at"],
        "peak_luminance": session["peak_luminance"],
        "target": {
            "gamut": target.gamut,
            "eotf": target.eotf,
            "peak_nits": target.peak_luminance_nits,
            "white_point": list(target.white_point_xy),
        },
        "pre_cal": pre,
        "post_cal": post,
        "white_balance": {"avg_de": wb["avg_de"], "max_de": wb["max_de"]},
        "color_tuner": {"avg_de": cms["avg_de"], "max_de": cms["max_de"]},
        "gamma": gamma,
        "improvement_pct": improvement,
        "wb_measurements": wb["measurements"],
        "cms_measurements": cms["measurements"],
        "gamma_measurements": gamma["measurements"],
    }


def render_report_pdf(report: Dict[str, Any]) -> bytes:
    try:
        import weasyprint
    except ImportError as exc:
        raise RuntimeError(
            "weasyprint is not installed. Run: pip install weasyprint"
        ) from exc
    html = render_report_html(report)
    return weasyprint.HTML(string=html).write_pdf()


def render_measurement_rows(measurements: List[Dict[str, Any]], *, include_gamma: bool = False) -> str:
    if not measurements:
        colspan = 5 if include_gamma else 4
        return f'<tr><td colspan="{colspan}">No measurements recorded</td></tr>'
    rows = []
    for measurement in measurements:
        cells = [
            escape(str(measurement.get("label", ""))),
            f'{float(measurement.get("Y", 0)):.1f}',
            f'{float(measurement.get("x", 0)):.4f}',
            f'{float(measurement.get("y", 0)):.4f}',
        ]
        if include_gamma:
            gamma_value = measurement.get("effective_gamma")
            cells.append("—" if gamma_value is None else f"{float(gamma_value):.3f}")
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return "".join(rows)


def render_report_html(report: Dict[str, Any]) -> str:
    def fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
        if value is None:
            return "—"
        if isinstance(value, (int, float)):
            return f"{value:.{digits}f}{suffix}"
        return f"{value}{suffix}"

    title = escape(f'{report["tv"]} Calibration Report')
    mode = escape(str(report["mode"]))
    date_str = escape(str(report["date"]))
    gamut = escape(str(report["target"]["gamut"]))
    eotf = escape(str(report["target"]["eotf"]))
    readiness = fmt(report["improvement_pct"], 1, "%")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{--bg:#f5f1e8;--card:#fffdf8;--ink:#1f1b16;--muted:#6f6558;--border:#d9cfbf;--accent:#0b6e4f;}}
    *{{box-sizing:border-box;}}
    body{{margin:0;font-family:Georgia,serif;color:var(--ink);background:var(--bg);}}
    .wrap{{max-width:1100px;margin:0 auto;padding:32px 20px 48px;}}
    .hero{{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:28px;margin-bottom:20px;}}
    .eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-bottom:8px;}}
    h1{{margin:0 0 8px;font-size:34px;}} h2{{margin:24px 0 12px;font-size:20px;}}
    .sub{{color:var(--muted);margin-bottom:16px;}}
    .pillrow{{display:flex;gap:10px;flex-wrap:wrap;}}
    .pill{{border:1px solid var(--border);border-radius:999px;padding:8px 12px;background:#faf6ef;font-size:14px;}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:20px;}}
    .card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px;}}
    .label{{color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;}}
    .value{{font-size:28px;font-weight:700;}}
    .section{{margin-top:24px;}}
    table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;}}
    th,td{{padding:10px 12px;border-bottom:1px solid var(--border);text-align:left;font-size:14px;}}
    th{{background:#f3ede3;color:var(--muted);font-weight:600;}}
    tr:last-child td{{border-bottom:none;}}
    .two-col{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;}}
    @media print {{
      body{{background:#fff;}}
      .wrap{{padding:16px;}}
      .hero{{border-radius:8px;}}
      .card{{border-radius:8px;}}
      table{{border-radius:8px;}}
      .section{{page-break-inside:avoid;}}
      .two-col > div{{page-break-inside:avoid;}}
      h2{{page-break-after:avoid;}}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">Calibration Report — ZRO Helper</div>
      <h1>{title}</h1>
      <div class="sub">{mode} · {date_str}</div>
      <div class="pillrow">
        <div class="pill">Target {gamut}</div>
        <div class="pill">EOTF {eotf}</div>
        <div class="pill">Improvement {readiness}</div>
      </div>
    </section>
    <section class="grid">
      <div class="card"><div class="label">Pre-Cal Avg ΔE</div><div class="value">{fmt(report["pre_cal"]["avg_de"])}</div></div>
      <div class="card"><div class="label">Post-Cal Avg ΔE</div><div class="value">{fmt(report["post_cal"]["avg_de"])}</div></div>
      <div class="card"><div class="label">Peak Luminance</div><div class="value">{fmt(report["peak_luminance"], 1, " nits")}</div></div>
      <div class="card"><div class="label">Avg Effective Gamma</div><div class="value">{fmt(report["gamma"]["avg_gamma"], 3)}</div></div>
    </section>
    <section class="section two-col">
      <div>
        <h2>Pre-Calibration Grayscale</h2>
        <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead>
        <tbody>{render_measurement_rows(report["pre_cal"]["measurements"])}</tbody></table>
      </div>
      <div>
        <h2>Post-Calibration Grayscale</h2>
        <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead>
        <tbody>{render_measurement_rows(report["post_cal"]["measurements"])}</tbody></table>
      </div>
    </section>
    <section class="section">
      <h2>White Balance</h2>
      <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead>
      <tbody>{render_measurement_rows(report["wb_measurements"])}</tbody></table>
    </section>
    <section class="section">
      <h2>Gamma Verification</h2>
      <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th><th>Effective Gamma</th></tr></thead>
      <tbody>{render_measurement_rows(report["gamma_measurements"], include_gamma=True)}</tbody></table>
    </section>
    <section class="section">
      <h2>Color Tuner</h2>
      <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead>
      <tbody>{render_measurement_rows(report["cms_measurements"])}</tbody></table>
    </section>
  </div>
</body>
</html>"""
