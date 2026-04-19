"""Report payload and HTML rendering extracted from server.py."""

from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

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
        "repass_count": session.get("repass_count", 0),
        "repass_reason": session.get("repass_decision", {}).get("reason", ""),
        "repass_action": session.get("repass_decision", {}).get("action", ""),
        "wb_measurements": wb["measurements"],
        "cms_measurements": cms["measurements"],
        "gamma_measurements": gamma["measurements"],
    }


def comparison_payload(
    session_a: Dict[str, Any],
    session_b: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a delta comparison payload between two calibration sessions.

    session_a is the baseline/earlier session; session_b is the more recent.
    Reports scalars for all metric fields; flags TV and mode mismatches so callers
    can surface a warning rather than crashing.
    """
    report_a = report_payload(session_a)
    report_b = report_payload(session_b)

    def _delta(val_b: Optional[float], val_a: Optional[float]) -> Optional[float]:
        if val_b is None or val_a is None:
            return None
        return round(val_b - val_a, 2)

    deltas: Dict[str, Any] = {
        "pre_cal_avg_de": _delta(
            report_b["pre_cal"]["avg_de"], report_a["pre_cal"]["avg_de"]
        ),
        "post_cal_avg_de": _delta(
            report_b["post_cal"]["avg_de"], report_a["post_cal"]["avg_de"]
        ),
        "pre_cal_max_de": _delta(
            report_b["pre_cal"]["max_de"], report_a["pre_cal"]["max_de"]
        ),
        "post_cal_max_de": _delta(
            report_b["post_cal"]["max_de"], report_a["post_cal"]["max_de"]
        ),
        "wb_avg_de": _delta(
            report_b["white_balance"]["avg_de"], report_a["white_balance"]["avg_de"]
        ),
        "cms_avg_de": _delta(
            report_b["color_tuner"]["avg_de"], report_a["color_tuner"]["avg_de"]
        ),
        "gamma_avg": _delta(
            report_b["gamma"]["avg_gamma"], report_a["gamma"]["avg_gamma"]
        ),
        "improvement_pct": _delta(
            report_b["improvement_pct"], report_a["improvement_pct"]
        ),
        "peak_luminance": _delta(
            report_b["peak_luminance"], report_a["peak_luminance"]
        ),
    }

    return {
        "session_a": {
            "id": session_a.get("id", ""),
            "date": session_a.get("created_at", ""),
            "mode": session_a.get("mode", ""),
            "tv": report_a["tv"],
            "report": report_a,
        },
        "session_b": {
            "id": session_b.get("id", ""),
            "date": session_b.get("created_at", ""),
            "mode": session_b.get("mode", ""),
            "tv": report_b["tv"],
            "report": report_b,
        },
        "deltas": deltas,
        "tv_mismatch": session_a.get("tv_key") != session_b.get("tv_key"),
        "mode_mismatch": session_a.get("mode") != session_b.get("mode"),
    }


def render_comparison_html(comparison: Dict[str, Any]) -> str:
    """Render a side-by-side HTML comparison report for two calibration sessions."""

    def fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
        if value is None:
            return "—"
        if isinstance(value, (int, float)):
            return f"{value:.{digits}f}{suffix}"
        return f"{value}{suffix}"

    def delta_cell(val: Optional[float], *, lower_is_better: bool = True) -> str:
        if val is None:
            return "<td>—</td>"
        improved = (val < 0) if lower_is_better else (val > 0)
        color = "#0b6e4f" if improved else "#c0392b"
        sign = "+" if val > 0 else ""
        return f'<td style="color:{color};font-weight:600">{sign}{val:.2f}</td>'

    sess_a = comparison["session_a"]
    sess_b = comparison["session_b"]
    rep_a = sess_a["report"]
    rep_b = sess_b["report"]
    deltas = comparison["deltas"]

    title_a = escape(f'{sess_a["tv"]} — {sess_a["mode"]} ({str(sess_a["date"])[:10]})')
    title_b = escape(f'{sess_b["tv"]} — {sess_b["mode"]} ({str(sess_b["date"])[:10]})')

    mismatch_banner = ""
    if comparison.get("tv_mismatch"):
        mismatch_banner = (
            '<div class="banner warn">⚠ Sessions are from different TV models — '
            "delta values may not be meaningful.</div>"
        )
    elif comparison.get("mode_mismatch"):
        mismatch_banner = (
            '<div class="banner warn">⚠ Sessions use different calibration modes — '
            "some metrics may not be directly comparable.</div>"
        )

    def stat_row(label: str, key_a: Any, key_b: Any, delta_key: str, lower_is_better: bool = True, digits: int = 2, suffix: str = "") -> str:
        return (
            f"<tr><th>{escape(label)}</th>"
            f"<td>{fmt(key_a, digits, suffix)}</td>"
            f"<td>{fmt(key_b, digits, suffix)}</td>"
            f"{delta_cell(deltas.get(delta_key), lower_is_better=lower_is_better)}</tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Calibration Comparison</title>
  <style>
    :root {{--bg:#f5f1e8;--card:#fffdf8;--ink:#1f1b16;--muted:#6f6558;--border:#d9cfbf;--accent:#0b6e4f;}}
    *{{box-sizing:border-box;}}
    body{{margin:0;font-family:Georgia,serif;color:var(--ink);background:var(--bg);}}
    .wrap{{max-width:1100px;margin:0 auto;padding:32px 20px 48px;}}
    .hero{{background:var(--card);border:1px solid var(--border);border-radius:18px;padding:28px;margin-bottom:20px;}}
    .eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-bottom:8px;}}
    h1{{margin:0 0 8px;font-size:28px;}} h2{{margin:24px 0 12px;font-size:20px;}}
    .banner{{padding:12px 16px;border-radius:10px;margin-bottom:16px;font-size:14px;}}
    .banner.warn{{background:#fff3cd;border:1px solid #ffc107;}}
    table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;margin-bottom:20px;}}
    th,td{{padding:10px 12px;border-bottom:1px solid var(--border);text-align:left;font-size:14px;}}
    thead th{{background:#f3ede3;color:var(--muted);font-weight:600;}}
    th:first-child{{width:220px;}}
    tr:last-child td,tr:last-child th{{border-bottom:none;}}
    .col-a{{background:#f0f8ff;}}
    .col-b{{background:#f0fff4;}}
    .col-delta{{background:#fafafa;}}
    .section{{margin-top:24px;}}
    @media print {{
      body{{background:#fff;}}
      .section{{page-break-inside:avoid;}}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">Session Comparison — ZRO Helper</div>
      <h1>Before / After Delta Report</h1>
    </section>
    {mismatch_banner}
    <section class="section">
      <h2>Key Metrics</h2>
      <table>
        <thead>
          <tr>
            <th>Metric</th>
            <th class="col-a">Session A (Before)<br><small>{title_a}</small></th>
            <th class="col-b">Session B (After)<br><small>{title_b}</small></th>
            <th class="col-delta">Δ (B − A)</th>
          </tr>
        </thead>
        <tbody>
          {stat_row("Pre-Cal Avg ΔE", rep_a["pre_cal"]["avg_de"], rep_b["pre_cal"]["avg_de"], "pre_cal_avg_de")}
          {stat_row("Pre-Cal Max ΔE", rep_a["pre_cal"]["max_de"], rep_b["pre_cal"]["max_de"], "pre_cal_max_de")}
          {stat_row("Post-Cal Avg ΔE", rep_a["post_cal"]["avg_de"], rep_b["post_cal"]["avg_de"], "post_cal_avg_de")}
          {stat_row("Post-Cal Max ΔE", rep_a["post_cal"]["max_de"], rep_b["post_cal"]["max_de"], "post_cal_max_de")}
          {stat_row("WB Avg ΔE", rep_a["white_balance"]["avg_de"], rep_b["white_balance"]["avg_de"], "wb_avg_de")}
          {stat_row("CMS Avg ΔE", rep_a["color_tuner"]["avg_de"], rep_b["color_tuner"]["avg_de"], "cms_avg_de")}
          {stat_row("Avg Gamma", rep_a["gamma"]["avg_gamma"], rep_b["gamma"]["avg_gamma"], "gamma_avg", lower_is_better=False, digits=3)}
          {stat_row("Improvement %", rep_a["improvement_pct"], rep_b["improvement_pct"], "improvement_pct", lower_is_better=False, digits=1, suffix="%")}
          {stat_row("Peak Luminance", rep_a["peak_luminance"], rep_b["peak_luminance"], "peak_luminance", lower_is_better=False, digits=1, suffix=" nits")}
        </tbody>
      </table>
    </section>
    <section class="section">
      <h2>Pre-Calibration Grayscale</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
          <h3 style="font-size:15px;margin:0 0 8px;">Session A</h3>
          <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead>
          <tbody>{render_measurement_rows(rep_a["pre_cal"]["measurements"])}</tbody></table>
        </div>
        <div>
          <h3 style="font-size:15px;margin:0 0 8px;">Session B</h3>
          <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead>
          <tbody>{render_measurement_rows(rep_b["pre_cal"]["measurements"])}</tbody></table>
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Post-Calibration Grayscale</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
          <h3 style="font-size:15px;margin:0 0 8px;">Session A</h3>
          <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead>
          <tbody>{render_measurement_rows(rep_a["post_cal"]["measurements"])}</tbody></table>
        </div>
        <div>
          <h3 style="font-size:15px;margin:0 0 8px;">Session B</h3>
          <table><thead><tr><th>Label</th><th>Nits</th><th>x</th><th>y</th></tr></thead>
          <tbody>{render_measurement_rows(rep_b["post_cal"]["measurements"])}</tbody></table>
        </div>
      </div>
    </section>
  </div>
</body>
</html>"""


def render_comparison_pdf(comparison: Dict[str, Any]) -> bytes:
    try:
        import weasyprint
    except ImportError as exc:
        raise RuntimeError(
            "weasyprint is not installed. Run: pip install weasyprint"
        ) from exc
    html = render_comparison_html(comparison)
    return weasyprint.HTML(string=html).write_pdf()


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
      .grid{{display:block;}}
      .card{{break-inside:avoid;page-break-inside:avoid;margin-bottom:12px;}}
      .two-col{{display:block;}}
      .two-col > div{{margin-bottom:16px;}}
      h2{{page-break-after:avoid;}}
      table{{break-inside:auto;page-break-inside:auto;}}
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
