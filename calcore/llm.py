from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .models import AnalysisConfig, LLMConfig, Summary


def _top_offenders(summary: Summary) -> Dict[str, Any]:
    """Extract worst-N patches by ΔE for compact LLM context (no raw XYZ)."""
    result: Dict[str, Any] = {}

    if summary.grayscale_rows:
        worst_gray = sorted(
            summary.grayscale_rows, key=lambda r: r.get("dE2000") or 0, reverse=True
        )[:3]
        result["worst_grayscale"] = [
            {
                "label": r["label"],
                "dE2000": round(r["dE2000"], 2) if r.get("dE2000") is not None else None,
                "gamma": round(r["gamma"], 3) if r.get("gamma") is not None else None,
                "pq_error_pct": round(r["pq_error_pct"], 1) if r.get("pq_error_pct") is not None else None,
            }
            for r in worst_gray
        ]

    if summary.color_rows:
        worst_color = sorted(
            summary.color_rows, key=lambda r: r.get("dE2000") or 0, reverse=True
        )[:3]
        result["worst_colors"] = [
            {
                "label": r["label"],
                "bucket": r.get("bucket"),
                "dE2000": round(r["dE2000"], 2) if r.get("dE2000") is not None else None,
                "dE2000_chroma_only": round(r["dE2000_chroma_only"], 2) if r.get("dE2000_chroma_only") is not None else None,
            }
            for r in worst_color
        ]

    return result


def build_llm_prompt(
    summary: Summary,
    cfg: AnalysisConfig,
    phase: str,
    guidance_context: Optional[str] = None,
    history_block: Optional[str] = None,
) -> str:
    # Exclude raw per-patch rows from the LLM payload — they waste tokens and
    # tempt the model to do per-patch math it can't reliably perform.
    # Instead, include a compact "top offenders" block (worst 3 by ΔE).
    summary_dict = {
        k: v
        for k, v in asdict(summary).items()
        if k not in ("grayscale_rows", "color_rows")
    }
    top = _top_offenders(summary)
    if top:
        summary_dict["top_offenders"] = top

    payload = {
        "phase": phase,
        "mode": cfg.mode,
        "eotf": cfg.eotf,
        "target_space": cfg.target_space,
        "summary": summary_dict,
    }

    parts: List[str] = [
        "You are a TV calibration co-pilot. Use the provided summary only. "
        "Do not do new math. Do not invent missing data. "
        "Return exactly one calibration step in this format:\n\n"
        "> **Step [Phase.Step]:** [Action title]\n"
        "> **Do this:** [Specific instruction]\n"
        "> **Why:** [1-2 sentences]\n"
        "> **Send me:** [What to send back]\n\n"
        "If the data is insufficient, say exactly what is missing and stop.",
    ]

    if history_block:
        parts.append(f"\nDISPLAY HISTORY (prior sessions for this TV):\n{history_block}")

    if guidance_context:
        parts.append(f"\nPRE-COMPUTED GUIDANCE (deterministic):\n{guidance_context}")

    parts.append(f"\nSUMMARY JSON:\n{json.dumps(payload, indent=2, default=str)}")

    return "\n".join(parts)


def resolve_endpoint(endpoint: str) -> str:
    """Normalise the endpoint URL to always point at /v1/chat/completions.

    Accepts any of:
      - Full URL:  http://localhost:4000/v1/chat/completions
      - Base URL:  http://localhost:4000/v1
      - Root URL:  http://localhost:4000
    """
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return endpoint + "/chat/completions"
    # Bare host or host+port - append the standard path.
    if not endpoint.endswith("/v1/chat/completions"):
        return endpoint + "/v1/chat/completions"
    return endpoint


def call_llm(
    summary: Summary,
    cfg: AnalysisConfig,
    phase: str,
    llm: LLMConfig,
    guidance_context: Optional[str] = None,
    history_block: Optional[str] = None,
) -> Optional[str]:
    """Call an OpenAI-compatible chat/completions endpoint (e.g. LiteLLM proxy)."""
    if not llm.endpoint or not llm.model:
        return None

    url = resolve_endpoint(llm.endpoint)
    prompt = build_llm_prompt(summary, cfg, phase, guidance_context=guidance_context, history_block=history_block)
    body = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": "You are a strict calibration assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": llm.temperature,
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # LiteLLM supports Bearer tokens; pass the key when provided.
    if llm.api_key:
        req.add_header("Authorization", f"Bearer {llm.api_key}")

    try:
        with urllib.request.urlopen(req, timeout=llm.timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"LLM HTTP error {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc

    parsed = json.loads(raw)
    try:
        return parsed["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        raise RuntimeError(f"LLM response missing content: {raw[:500]}") from exc


def hash_summary(summary: Summary) -> str:
    blob = json.dumps(
        {
            "meta": summary.meta,
            "gray": [
                summary.grayscale_avg_de,
                summary.grayscale_max_de,
                summary.grayscale_over_3,
                summary.gamma_midtones,
                summary.pq_err_midtones,
            ],
            "color": [
                summary.color_75_avg_de,
                summary.color_75_max_de,
                summary.color_100_avg_de,
                summary.color_100_max_de,
            ],
        },
        sort_keys=True,
        default=str,
    )
    return str(abs(hash(blob)))


def build_history_block(
    history: List[Dict[str, Any]],
    baseline: Optional[Dict[str, Any]] = None,
) -> str:
    """Format per-TV calibration history as a compact text block for LLM context.

    Limits to 3 most-recent sessions + baseline to stay within ~800 tokens.
    """
    if not history:
        return ""

    lines: List[str] = []
    recent = history[:3]

    for entry in recent:
        date = (entry.get("date") or "unknown")[:10]
        pre = entry.get("pre_grayscale_avg_de")
        post = entry.get("post_grayscale_avg_de")
        peak = entry.get("peak_luminance")
        gamma_avg = entry.get("gamma_avg")
        mode = entry.get("mode", "")

        parts: List[str] = []
        if pre is not None:
            parts.append(f"Pre ΔE={pre:.1f}")
        if post is not None:
            parts.append(f"Post ΔE={post:.1f}")
        if gamma_avg is not None:
            parts.append(f"Gamma={gamma_avg:.2f}")
        if peak is not None:
            parts.append(f"Peak={peak:.0f} nit")
        if mode:
            parts.append(f"[{mode}]")

        lines.append(f"{date}: {'. '.join(parts)}.")

        compromises = entry.get("accepted_compromises") or []
        if compromises:
            lines.append(f"  Accepted compromises: {', '.join(compromises)}.")

    # Compute peak luminance trend if we have 2+ sessions
    peaks = [e.get("peak_luminance") for e in history if e.get("peak_luminance")]
    if len(peaks) >= 2:
        avg_drop_per_session = (peaks[-1] - peaks[0]) / (len(peaks) - 1)
        if avg_drop_per_session < -2:
            lines.append(
                f"TREND: Peak luminance declining ~{abs(avg_drop_per_session):.1f} nit/session "
                "(expected panel aging)."
            )

    if baseline:
        b_date = (baseline.get("date") or "")[:10]
        b_pre = baseline.get("pre_grayscale_avg_de")
        b_peak = baseline.get("peak_luminance")
        b_parts: List[str] = []
        if b_pre is not None:
            b_parts.append(f"Pre ΔE={b_pre:.1f}")
        if b_peak is not None:
            b_parts.append(f"Peak={b_peak:.0f} nit")
        if b_parts:
            lines.append(f"BASELINE ({b_date}): {'. '.join(b_parts)}.")

    return "\n".join(lines)


@dataclass
class PatchStrategy:
    """LLM-recommended patch sequencing adjustment for the next measurement pass."""
    focus: str               # e.g. "grayscale_fine", "color_blue", "cms_secondary"
    rationale: str           # plain-English explanation shown to user
    add_patches: List[str]   # patch labels to inject next (e.g. ["White 35%", "White 45%"])
    skip_patches: List[str]  # patch labels to defer (e.g. ["Cyan 75%"])
    confidence: float        # 0–1; below 0.6 = don't auto-apply, surface for user review


def query_next_patch_strategy(
    current_summary: Summary,
    session_history: List[Dict[str, Any]],
    phase: str,
    budget_remaining: int,
    llm: LLMConfig,
) -> Optional[PatchStrategy]:
    """Ask the LLM which patches to prioritize or skip next.

    Returns None if LLM is not configured or the response cannot be parsed.
    budget_remaining: estimated patches left before the report step.
    """
    if not llm.endpoint or not llm.model:
        return None

    # Compact summary (scalar metrics only, no rows)
    summary_dict = {
        k: v
        for k, v in asdict(current_summary).items()
        if k not in ("grayscale_rows", "color_rows")
    }
    top = _top_offenders(current_summary)
    if top:
        summary_dict["top_offenders"] = top

    # Compact prior-step history for this session
    history_items = []
    for h in session_history[-3:]:
        history_items.append({
            "phase": h.get("phase"),
            "grayscale_avg_de": h.get("grayscale_avg_de"),
            "gamma_midtones": h.get("gamma_midtones"),
            "color_100_avg_de": h.get("color_100_avg_de"),
        })

    payload = {
        "current_phase": phase,
        "budget_remaining": budget_remaining,
        "current_summary": summary_dict,
        "session_step_history": history_items,
    }

    prompt = (
        "You are a TV calibration measurement strategist. "
        "Based on the current measurement summary, decide which patches to prioritize or skip next.\n\n"
        "Respond with ONLY a JSON object in this exact schema (no markdown, no extra text):\n"
        "{\n"
        '  "focus": "<grayscale_fine|color_red|color_blue|color_green|cms_secondary|confirm_only>",\n'
        '  "rationale": "<1-2 sentences explaining why>",\n'
        '  "add_patches": ["<label>", ...],\n'
        '  "skip_patches": ["<label>", ...],\n'
        '  "confidence": <0.0-1.0>\n'
        "}\n\n"
        f"PAYLOAD:\n{json.dumps(payload, indent=2, default=str)}"
    )

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": "You are a strict JSON-only responder. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    if llm.api_key:
        req.add_header("Authorization", f"Bearer {llm.api_key}")

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 30.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"].strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        obj = json.loads(content)
        return PatchStrategy(
            focus=str(obj.get("focus", "confirm_only")),
            rationale=str(obj.get("rationale", "")),
            add_patches=list(obj.get("add_patches") or []),
            skip_patches=list(obj.get("skip_patches") or []),
            confidence=float(obj.get("confidence", 0.5)),
        )
    except Exception:
        return None


def query_remediation(
    event_type: str,
    context: Dict[str, Any],
    attempt_count: int,
    session_phase: str,
    llm: LLMConfig,
) -> Optional[Dict[str, Any]]:
    """Ask the LLM to diagnose a hardware fault and suggest recovery steps.

    Returns a dict with keys: steps (list of str), explanation (str), confidence (float).
    Returns None if LLM is not configured or response cannot be parsed.

    The LLM diagnoses WHICH recovery path; the caller executes deterministic steps.
    """
    if not llm.endpoint or not llm.model:
        return None

    payload = {
        "event_type": event_type,
        "context": context,
        "attempt_count": attempt_count,
        "session_phase": session_phase,
    }

    prompt = (
        "You are a TV calibration hardware troubleshooter. "
        "A hardware event has occurred during a calibration session. "
        "Recommend a recovery plan.\n\n"
        "Valid recovery actions:\n"
        "  - retry_measurement: Re-take the last measurement patch\n"
        "  - restart_dogegen: Kill and restart the Dogegen pattern generator process\n"
        "  - reconnect_adb: Run 'adb connect' to re-establish TV connection\n"
        "  - discard_outlier: Drop the anomalous measurement and re-queue it\n"
        "  - relax_cms_target: Suggest reducing CMS target saturation to 75%\n"
        "  - notify_user: Surface the issue for manual user intervention\n\n"
        "Respond with ONLY a JSON object (no markdown, no extra text):\n"
        "{\n"
        '  "steps": ["<action>", ...],\n'
        '  "explanation": "<1-2 sentences>",\n'
        '  "confidence": <0.0-1.0>\n'
        "}\n\n"
        f"EVENT:\n{json.dumps(payload, indent=2, default=str)}"
    )

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": "You are a strict JSON-only responder. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    if llm.api_key:
        req.add_header("Authorization", f"Bearer {llm.api_key}")

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 20.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        obj = json.loads(content)
        return {
            "steps": list(obj.get("steps") or []),
            "explanation": str(obj.get("explanation", "")),
            "confidence": float(obj.get("confidence", 0.5)),
        }
    except Exception:
        return None


def query_gamut_advice(
    diagnosis_text: str,
    target_space: str,
    llm: LLMConfig,
) -> Optional[str]:
    """Ask the LLM to interpret a GamutDiagnosis and suggest compromise wording.

    diagnosis_text: pre-formatted string from format_gamut_diagnosis().
    Returns plain-English recommendation for the user, or None on failure.
    """
    if not llm.endpoint or not llm.model:
        return None

    prompt = (
        "You are an expert TV colorist advising on CMS gamut trade-offs. "
        "The following gamut feasibility report describes how the measured display primaries "
        f"compare to the {target_space} target. Some primaries may be outside the ADB CMS correction range.\n\n"
        "For each primary that is outside correction range, recommend in plain English:\n"
        "1. Whether to correct to target (losing luminance) or accept panel native (slight hue error)\n"
        "2. What the viewer will notice in practice\n"
        "3. A specific compromise value if applicable (e.g. 'target 75% saturation instead of 100%')\n\n"
        "Be concise — one paragraph per affected primary.\n\n"
        f"GAMUT DIAGNOSIS:\n{diagnosis_text}"
    )

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": "You are a strict calibration assistant. Be concise and technical."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    if llm.api_key:
        req.add_header("Authorization", f"Bearer {llm.api_key}")

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 30.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        return parsed["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


_resolve_endpoint = resolve_endpoint
call_local_llm = call_llm
