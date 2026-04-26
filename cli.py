#!/usr/bin/env python3
"""TV calibration coach CLI built on the calcore package."""

from __future__ import annotations

import argparse
import json
import math
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from calcore import (
    AnalysisConfig,
    LLMConfig,
    SessionState,
    analyze,
    call_llm,
    determine_phase,
    parse_measurement_csv,
)
from calcore.llm import hash_summary, query_delta_summary
from calcore.models import Summary


logger = logging.getLogger(__name__)


def format_num(v: Optional[float], digits: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return "n/a"
    return f"{v:.{digits}f}"


def recommendation(summary: Summary, phase: str, cfg: AnalysisConfig) -> str:
    gray_ok = (
        summary.grayscale_avg_de is not None
        and summary.grayscale_avg_de <= 2.0
        and (summary.grayscale_max_de or 999) <= 3.0
    )
    if cfg.mode.lower() == "hdr" or cfg.eotf.lower() == "pq":
        eotf_ok = (
            summary.pq_err_midtones is not None
            and abs(summary.pq_err_midtones) <= 5.0
        )
    else:
        eotf_ok = (
            summary.gamma_midtones is not None
            and abs(summary.gamma_midtones - 2.2) <= 0.1
        )

    color75_ok = summary.color_75_avg_de is not None and summary.color_75_avg_de <= 3.0
    color100_ok = (
        summary.color_100_avg_de is not None and summary.color_100_avg_de <= 3.0
    )

    if phase == "baseline":
        return (
            "Run your grayscale baseline sweep and color sweeps, then paste the CSV. "
            "This establishes the before snapshot and tells us whether grayscale or gamut is the bigger problem."
        )

    if phase in ("wb", "mpwb"):
        if gray_ok and eotf_ok:
            return (
                "Grayscale is close enough to move on. Start CMS next, but only if the color patches are present in the CSV."
            )
        return (
            "Stay in white balance. Fix low-IRE first if the shadows are off, then high-IRE gain. "
            "Re-run the grayscale sweep after each adjustment set."
        )

    if phase == "cms":
        if color75_ok and color100_ok:
            return (
                "CMS is in good shape. Run the final verification sweep and save the report."
            )
        return (
            "Stay in CMS and correct hue/saturation only. Leave luminance controls alone unless the panel is clearly clipping or the model is known to behave well."
        )

    if phase == "verify":
        return (
            "You are done with this mode. Save the report, then move to the next mode only after a fresh Phase 0 baseline."
        )

    return "Continue measuring and re-run the script when the CSV updates."


def print_report(
    summary: Summary,
    cfg: AnalysisConfig,
    phase: str,
    llm_text: Optional[str] = None,
) -> None:
    print("\n" + "═" * 78)
    print("TV CALIBRATION LIVE REPORT")
    print("═" * 78)
    print(f"Mode:          {cfg.mode.upper()}")
    print(f"EOTF:          {cfg.eotf}")
    print(f"Target space:   {cfg.target_space}")
    print(f"Code max:       {cfg.code_max}")
    print(f"Phase:          {phase}")
    print(
        f"Patches:        {summary.meta['patch_count']} total | {summary.meta['grayscale_count']} grayscale | {summary.meta['color_count']} color"
    )
    print(f"Peak Y:         {format_num(summary.meta['measured_peak_y'])}")
    print(f"Black Y:        {format_num(summary.meta['measured_black_y'])}")
    print()
    print("GRAYSCALE")
    print(f"  Avg dE2000:   {format_num(summary.grayscale_avg_de)}")
    print(f"  Max dE2000:   {format_num(summary.grayscale_max_de)}")
    print(f"  dE > 3:       {summary.grayscale_over_3}")
    if summary.gamma_midtones is not None:
        print(f"  Avg gamma:    {format_num(summary.gamma_midtones)}")
    if summary.pq_err_midtones is not None:
        print(f"  Mid PQ err:   {format_num(summary.pq_err_midtones)} %")
    print()
    print("COLOR")
    print(f"  75% avg dE:   {format_num(summary.color_75_avg_de)}")
    print(f"  75% max dE:   {format_num(summary.color_75_max_de)}")
    print(f"  75% chroma:   {format_num(summary.color_75_chroma_avg)}")
    print(f"  100% avg dE:  {format_num(summary.color_100_avg_de)}")
    print(f"  100% max dE:  {format_num(summary.color_100_max_de)}")
    print(f"  100% chroma:  {format_num(summary.color_100_chroma_avg)}")
    print()
    print("NEXT STEP")
    if llm_text:
        print("  LLM guidance:")
        for line in llm_text.splitlines():
            print(f"  {line}")
    else:
        print(f"  {recommendation(summary, phase, cfg)}")
    print("═" * 78 + "\n")


def save_state(path: Path, state: SessionState) -> None:
    payload = {
        "phase": state.phase,
        "last_mtime": state.last_mtime,
        "last_report_hash": state.last_report_hash,
        "config": asdict(state.config),
        "llm": asdict(state.llm),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_state(path: Path, default_cfg: AnalysisConfig) -> SessionState:
    if not path.exists():
        return SessionState(config=default_cfg)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cfg = raw.get("config", {})
        llm_raw = raw.get("llm", {})
        return SessionState(
            phase=raw.get("phase", "baseline"),
            last_mtime=float(raw.get("last_mtime", 0.0)),
            last_report_hash=str(raw.get("last_report_hash", "")),
            config=AnalysisConfig(
                mode=cfg.get("mode", default_cfg.mode),
                eotf=cfg.get("eotf", default_cfg.eotf),
                target_space=cfg.get("target_space", default_cfg.target_space),
                code_max=int(cfg.get("code_max", default_cfg.code_max)),
            ),
            llm=LLMConfig.from_dict(llm_raw, default_timeout=120.0),
        )
    except Exception as exc:
        logger.warning("Failed to load session state from %s; starting fresh", path, exc_info=True)
        return SessionState(config=default_cfg)


def run_once(csv_path: Path, state: SessionState) -> SessionState:
    patches = parse_measurement_csv(str(csv_path))
    summary = analyze(patches, state.config)
    new_phase = determine_phase(summary, state.phase)
    state.phase = new_phase
    state.last_report_hash = hash_summary(summary)

    llm_text: Optional[str] = None
    try:
        llm_text = call_local_llm(summary, state.config, state.phase, state.llm)
    except Exception as exc:
        print(f"[llm unavailable] {exc}\n")

    print_report(summary, state.config, state.phase, llm_text=llm_text)
    return state


def watch(csv_path: Path, state_path: Path, state: SessionState, interval: float) -> None:
    print(f"Watching: {csv_path}")
    print(
        "Save over the CSV after each measurement sweep and this script will refresh the analysis automatically.\n"
    )
    while True:
        try:
            mtime = csv_path.stat().st_mtime
        except FileNotFoundError:
            print(f"Waiting for file: {csv_path}")
            time.sleep(interval)
            continue

        if mtime > state.last_mtime:
            try:
                state = run_once(csv_path, state)
                state.last_mtime = mtime
                save_state(state_path, state)
            except Exception as exc:
                print(f"\n[error] {exc}\n")
        time.sleep(interval)


def _compare_sessions(
    path_a: Path,
    path_b: Path,
    llm_cfg: Optional[LLMConfig] = None,
) -> None:
    """Print a before/after delta comparison for two session JSON report files."""
    import json as _json

    def _load_report(p: Path) -> Dict[str, Any]:
        raw = _json.loads(p.read_text(encoding="utf-8"))
        # Accept either a raw session JSON or an already-computed report payload.
        # A session JSON has "pre_measurements"; a report payload has "pre_cal".
        if "pre_cal" in raw:
            return raw
        # Build a minimal synthetic report from history-style entries
        pre_de = raw.get("pre_grayscale_avg_de")
        post_de = raw.get("post_grayscale_avg_de")
        return {
            "tv": raw.get("tv", str(p)),
            "mode": raw.get("mode", ""),
            "date": raw.get("date", ""),
            "peak_luminance": raw.get("peak_luminance"),
            "pre_cal": {"avg_de": pre_de, "max_de": None, "measurements": []},
            "post_cal": {"avg_de": post_de, "max_de": None, "measurements": []},
            "white_balance": {"avg_de": raw.get("wb_avg_de"), "max_de": None},
            "color_tuner": {"avg_de": raw.get("cms_avg_de"), "max_de": None},
            "gamma": {"avg_gamma": raw.get("gamma_avg"), "measurements": []},
            "improvement_pct": raw.get("improvement_pct"),
            "target": {},
            "wb_measurements": [],
            "cms_measurements": [],
            "gamma_measurements": [],
        }

    rep_a = _load_report(path_a)
    rep_b = _load_report(path_b)

    def _delta(b_val: Optional[float], a_val: Optional[float]) -> str:
        if b_val is None or a_val is None:
            return "n/a"
        d = b_val - a_val
        sign = "+" if d > 0 else ""
        return f"{sign}{d:.2f}"

    deltas: Dict[str, Any] = {}
    for key, (b_path, a_path) in {
        "pre_cal_avg_de": (rep_b["pre_cal"]["avg_de"], rep_a["pre_cal"]["avg_de"]),
        "post_cal_avg_de": (rep_b["post_cal"]["avg_de"], rep_a["post_cal"]["avg_de"]),
        "wb_avg_de": (rep_b["white_balance"]["avg_de"], rep_a["white_balance"]["avg_de"]),
        "cms_avg_de": (rep_b["color_tuner"]["avg_de"], rep_a["color_tuner"]["avg_de"]),
        "gamma_avg": (rep_b["gamma"]["avg_gamma"], rep_a["gamma"]["avg_gamma"]),
        "improvement_pct": (rep_b["improvement_pct"], rep_a["improvement_pct"]),
        "peak_luminance": (rep_b["peak_luminance"], rep_a["peak_luminance"]),
    }.items():
        if b_path is not None and a_path is not None:
            deltas[key] = round(b_path - a_path, 3)

    print("\n" + "═" * 78)
    print("BEFORE / AFTER DELTA REPORT")
    print("═" * 78)
    print(f"Session A (Before): {rep_a.get('tv', '')} — {rep_a.get('mode', '')} — {str(rep_a.get('date', ''))[:10]}")
    print(f"Session B (After):  {rep_b.get('tv', '')} — {rep_b.get('mode', '')} — {str(rep_b.get('date', ''))[:10]}")
    print()
    print(f"{'Metric':<30} {'Session A':>12} {'Session B':>12} {'Δ (B-A)':>12}")
    print("─" * 70)

    def _row(label: str, a_val: Any, b_val: Any, delta_key: str, digits: int = 2, suffix: str = "") -> None:
        a_str = f"{a_val:.{digits}f}{suffix}" if a_val is not None else "—"
        b_str = f"{b_val:.{digits}f}{suffix}" if b_val is not None else "—"
        d_str = _delta(b_val, a_val)
        print(f"{label:<30} {a_str:>12} {b_str:>12} {d_str:>12}")

    _row("Pre-Cal Avg ΔE", rep_a["pre_cal"]["avg_de"], rep_b["pre_cal"]["avg_de"], "pre_cal_avg_de")
    _row("Post-Cal Avg ΔE", rep_a["post_cal"]["avg_de"], rep_b["post_cal"]["avg_de"], "post_cal_avg_de")
    _row("WB Avg ΔE", rep_a["white_balance"]["avg_de"], rep_b["white_balance"]["avg_de"], "wb_avg_de")
    _row("CMS Avg ΔE", rep_a["color_tuner"]["avg_de"], rep_b["color_tuner"]["avg_de"], "cms_avg_de")
    _row("Avg Gamma", rep_a["gamma"]["avg_gamma"], rep_b["gamma"]["avg_gamma"], "gamma_avg", digits=3)
    _row("Improvement %", rep_a["improvement_pct"], rep_b["improvement_pct"], "improvement_pct", digits=1, suffix="%")
    _row("Peak Luminance", rep_a["peak_luminance"], rep_b["peak_luminance"], "peak_luminance", digits=1, suffix=" nit")
    print("═" * 78)

    if llm_cfg and llm_cfg.endpoint and llm_cfg.model:
        print("\nGenerating LLM delta summary...")
        try:
            summary_text = query_delta_summary(rep_a, rep_b, deltas, llm_cfg)
            if summary_text:
                print("\nLLM ANALYSIS:")
                for line in summary_text.splitlines():
                    print(f"  {line}")
        except Exception as exc:
            print(f"[llm unavailable] {exc}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Live calibration coach for ColourSpace CSV files."
    )
    subparsers = ap.add_subparsers(dest="command")

    # ── compare subcommand ────────────────────────────────────────────────
    compare_p = subparsers.add_parser(
        "compare",
        help="Compare two session report JSON files and print a delta table.",
    )
    compare_p.add_argument(
        "session_a",
        metavar="SESSION_A",
        help="Path to the baseline session report JSON (before).",
    )
    compare_p.add_argument(
        "session_b",
        metavar="SESSION_B",
        help="Path to the more recent session report JSON (after).",
    )
    compare_p.add_argument(
        "--llm-endpoint",
        default=os.environ.get("TVCAL_LLM_ENDPOINT", ""),
        help="OpenAI-compatible endpoint for LLM delta summary (optional).",
    )
    compare_p.add_argument(
        "--llm-model",
        default=os.environ.get("TVCAL_LLM_MODEL", ""),
        help="Model name for LLM delta summary (optional).",
    )
    compare_p.add_argument(
        "--llm-api-key",
        default=os.environ.get("TVCAL_LLM_API_KEY", ""),
        help="API key for LLM endpoint.",
    )

    # ── watch/run subcommand (original behaviour) ─────────────────────────
    ap.add_argument("--csv", help="Path to the measurement CSV file")
    ap.add_argument("--mode", choices=["sdr", "hdr"], default="hdr", help="Calibration mode")
    ap.add_argument(
        "--eotf",
        default="pq",
        help="pq, gamma22, bt1886, or a numeric gamma",
    )
    ap.add_argument(
        "--target-space",
        default="p3d65",
        help="bt709, p3d65, or bt2020",
    )
    ap.add_argument("--code-max", type=int, default=1023, help="Digital code maximum")
    ap.add_argument("--watch", action="store_true", help="Watch the CSV for changes")
    ap.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds when watching",
    )
    ap.add_argument("--state", default="tv_calibration_state.json", help="State file path")
    ap.add_argument(
        "--llm-endpoint",
        default=os.environ.get("TVCAL_LLM_ENDPOINT", ""),
        help="OpenAI-compatible endpoint base URL or full chat/completions URL (e.g. http://localhost:4000 for LiteLLM)",
    )
    ap.add_argument(
        "--llm-model",
        default=os.environ.get("TVCAL_LLM_MODEL", ""),
        help="Model name passed to the endpoint (e.g. gpt-4o, claude-3-5-sonnet, ollama/llama3)",
    )
    ap.add_argument(
        "--llm-api-key",
        default=os.environ.get("TVCAL_LLM_API_KEY", ""),
        help="Bearer token / API key for the endpoint (use TVCAL_LLM_API_KEY env var to avoid shell history)",
    )
    ap.add_argument(
        "--llm-temperature",
        type=float,
        default=float(os.environ.get("TVCAL_LLM_TEMPERATURE", "0.2")),
        help="LLM temperature",
    )
    ap.add_argument(
        "--llm-timeout",
        type=float,
        default=float(os.environ.get("TVCAL_LLM_TIMEOUT", "120")),
        help="LLM timeout seconds",
    )
    args = ap.parse_args()

    # ── compare subcommand ────────────────────────────────────────────────
    if args.command == "compare":
        llm_cfg: Optional[LLMConfig] = None
        if args.llm_endpoint and args.llm_model:
            llm_cfg = LLMConfig(
                endpoint=args.llm_endpoint.strip(),
                model=args.llm_model.strip(),
                api_key=getattr(args, "llm_api_key", "").strip(),
            )
        _compare_sessions(Path(args.session_a), Path(args.session_b), llm_cfg)
        return

    if not args.csv:
        ap.error("--csv is required when not using a subcommand")

    cfg = AnalysisConfig(
        mode=args.mode,
        eotf=args.eotf,
        target_space=args.target_space,
        code_max=args.code_max,
    )
    state_path = Path(args.state)
    state = load_state(state_path, cfg)
    state.config = cfg
    state.llm = LLMConfig(
        endpoint=args.llm_endpoint.strip(),
        model=args.llm_model.strip(),
        api_key=args.llm_api_key.strip(),
        temperature=args.llm_temperature,
        timeout=args.llm_timeout,
    )

    csv_path = Path(args.csv)

    if args.watch:
        watch(csv_path, state_path, state, args.interval)
        return

    state = run_once(csv_path, state)
    save_state(state_path, state)


call_local_llm = call_llm


if __name__ == "__main__":
    main()
