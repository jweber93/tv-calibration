from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .models import AnalysisConfig, LLMConfig, Summary, TVSettings

# Max repasses before flagging hardware ceiling
_REPATCH_MAX_PASSES = 3

# Conservative Round-1 correction factor for inter-node bleed (QE_AUDIT.md
# ground truth). The prompt asks the model to self-damp; this is the
# deterministic backstop so the invariant holds even if it doesn't (#554).
_ROUND1_DAMPING_FACTOR = 0.55

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```\s*(?:json\s*)?(.*?)\s*```", re.IGNORECASE | re.DOTALL)


def _extract_json(text: str) -> str:
    """Extract JSON from a string that may be wrapped in markdown code fences.

    Handles:
      - ```json ... ``` (lowercase)
      - ```JSON ... ``` (uppercase)
      - ``` ... ``` (no language tag)
      - Prose prefix before the fence
      - Newline between fence and language tag
      - Raw JSON with no fences (passes through)

    If no fence is found, falls back to brace-balancing: finds the first '{'
    and returns the matching balanced substring.
    """
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # Fallback: find the first '{' and attempt to balance to the matching '}'
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _safe_float(value: Any) -> Optional[float]:
    """Coerce a client-supplied value to float, or None if it isn't numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
                "dE2000": round(r["dE2000"], 2)
                if r.get("dE2000") is not None
                else None,
                "gamma": round(r["gamma"], 3) if r.get("gamma") is not None else None,
                "pq_error_pct": round(r["pq_error_pct"], 1)
                if r.get("pq_error_pct") is not None
                else None,
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
                "dE2000": round(r["dE2000"], 2)
                if r.get("dE2000") is not None
                else None,
                "dE2000_chroma_only": round(r["dE2000_chroma_only"], 2)
                if r.get("dE2000_chroma_only") is not None
                else None,
            }
            for r in worst_color
        ]

    return result


@dataclass
class AdjustmentPlan:
    """Structured LLM output: a list of hardware adjustments + next-step directive."""

    adjustments: List[Dict[str, Any]]  # list of adjustment dicts per schema below
    next_step: str  # "rerun_grayscale" | "proceed_cms" | "rerun_wb" | "verify"
    confidence: float  # 0.0–1.0

    # Adjustment dict schema (each item):
    # {
    #   "menu":    str         — TV menu name
    #   "setting": str         — setting name
    #   "from":    int|float   — current value (if known)
    #   "to":      int|float   — target value
    #   "scope":   "global" | "local"
    #   "reason":  str         — 1-2 sentences, physics-based
    # }


def parse_adjustment_plan(text: str) -> Optional[AdjustmentPlan]:
    """Parse a JSON LLM response into an AdjustmentPlan.

    Strips markdown code fences if present (defensive, same pattern as
    query_next_patch_strategy).  Returns None if the text cannot be parsed or
    required fields are missing.
    """
    content = _extract_json(text)
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None

    adjustments = obj.get("adjustments")
    next_step = obj.get("next_step")
    confidence = obj.get("confidence")

    if not isinstance(adjustments, list) or not next_step:
        return None

    required_fields: set = {"menu", "setting", "to", "scope"}
    for adj in adjustments:
        if not isinstance(adj, dict) or not required_fields.issubset(adj.keys()):
            logger.warning("parse_adjustment_plan: adjustment missing required key(s)")
            return None
        for field in required_fields:
            if adj[field] is None:
                logger.warning(
                    "parse_adjustment_plan: required field '%s' is null", field
                )
                return None

    try:
        return AdjustmentPlan(
            adjustments=adjustments,
            next_step=str(next_step),
            confidence=float(confidence) if confidence is not None else 0.5,
        )
    except (TypeError, ValueError):
        return None


def build_llm_prompt(
    summary: Summary,
    cfg: AnalysisConfig,
    phase: str,
    guidance_context: Optional[str] = None,
    history_block: Optional[str] = None,
    tv_settings: Optional[TVSettings] = None,
    tv_schema: Optional[Dict[str, Any]] = None,
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

    payload: Dict[str, Any] = {
        "phase": phase,
        "mode": cfg.mode,
        "eotf": cfg.eotf,
        "target_space": cfg.target_space,
        "summary": summary_dict,
    }

    # Inject current TV hardware slider values when supplied (#96)
    if tv_settings is not None:
        ts_dict = {k: v for k, v in asdict(tv_settings).items() if v is not None}
        if ts_dict:
            payload["current_tv_settings"] = ts_dict

    # Inject TV model settings schema (ranges, menu paths) when available (#99)
    if tv_schema:
        payload["tv_settings_schema"] = tv_schema

    # Structured JSON output schema instructions
    schema_instructions = (
        "Respond with ONLY a valid JSON object matching this exact schema "
        "(no markdown, no prose outside the JSON):\n"
        "{\n"
        '  "adjustments": [\n'
        "    {\n"
        '      "menu": "<TV menu name>",\n'
        '      "setting": "<setting name>",\n'
        '      "from": <current value or null>,\n'
        '      "to": <target value>,\n'
        '      "scope": "global" | "local",\n'
        '      "reason": "<1-2 sentences, physics-based>"\n'
        "    }\n"
        "  ],\n"
        '  "next_step": "rerun_grayscale" | "proceed_cms" | "rerun_wb" | "verify",\n'
        '  "confidence": <0.0-1.0>\n'
        "}\n\n"
        '"scope" distinguishes Global errors (2-point Gain/Offset) from Local errors '
        "(multi-point / EOTF adjustment).\n"
        "If the data is insufficient to produce adjustments, return an empty "
        '"adjustments" array with "next_step": "rerun_grayscale" and '
        '"confidence": 0.0.'
    )

    parts: List[str] = [schema_instructions]

    if history_block:
        parts.append(
            f"\nDISPLAY HISTORY (prior sessions for this TV):\n{history_block}"
        )

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


def _build_request(url: str, body: Dict[str, Any], llm: LLMConfig) -> urllib.request.Request:
    """Build a POST request to an OpenAI-compatible endpoint with provider headers.

    Centralises the shared request-building boilerplate (JSON body, Bearer auth)
    and injects provider-specific headers so every call site sends identical
    headers — including the connection probe.  For ``provider == "openrouter"``
    this adds:

      - ``HTTP-Referer`` — per-app rate-limit attribution (only when set)
      - ``X-Title`` — display name shown in OpenRouter's usage dashboard
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if llm.api_key:
        req.add_header("Authorization", f"Bearer {llm.api_key}")
    if llm.provider == "openrouter":
        if llm.http_referer:
            req.add_header("HTTP-Referer", llm.http_referer)
        req.add_header("X-Title", llm.app_title or "tv-calibration")
    return req


_SYSTEM_PROMPT = (
    "You are an expert Display Calibration Engine. "
    "Identify the highest-dE errors and produce precise hardware adjustment deltas. "
    "Respond only with valid JSON. No prose, no markdown, no explanations outside the JSON schema."
)


def call_llm(
    summary: Summary,
    cfg: AnalysisConfig,
    phase: str,
    llm: LLMConfig,
    guidance_context: Optional[str] = None,
    history_block: Optional[str] = None,
    tv_settings: Optional[TVSettings] = None,
    tv_schema: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Call an OpenAI-compatible chat/completions endpoint (e.g. LiteLLM proxy).

    Returns the raw JSON string from the model (to be parsed by
    parse_adjustment_plan), or a deferred-message string when the sweep is
    incomplete, or None if the LLM is not configured.
    """
    if not llm.endpoint or not llm.model:
        return None

    # Gate: defer analysis on partial sweeps (#97)
    if not summary.is_sweep_complete:
        measured = summary.measured_patch_count
        expected = summary.expected_patch_count
        expected_str = str(expected) if expected is not None else "?"
        return (
            f"Partial sweep received ({measured}/{expected_str} patches) "
            f"— awaiting full {phase} sweep before providing adjustments."
        )

    url = resolve_endpoint(llm.endpoint)
    prompt = build_llm_prompt(
        summary,
        cfg,
        phase,
        guidance_context=guidance_context,
        history_block=history_block,
        tv_settings=tv_settings,
        tv_schema=tv_schema,
    )
    body = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    req = _build_request(url, body, llm)

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

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(
            f"LLM returned non-JSON body: {raw[:500]}"
        ) from exc
    choices = parsed.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM returned empty choices array: {raw[:500]}")
    try:
        return choices[0]["message"]["content"].strip()
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

    focus: str  # e.g. "grayscale_fine", "color_blue", "cms_secondary"
    rationale: str  # plain-English explanation shown to user
    add_patches: List[
        str
    ]  # patch labels to inject next (e.g. ["White 35%", "White 45%"])
    skip_patches: List[str]  # patch labels to defer (e.g. ["Cyan 75%"])
    confidence: float  # 0–1; below 0.6 = don't auto-apply, surface for user review


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
        history_items.append(
            {
                "phase": h.get("phase"),
                "grayscale_avg_de": h.get("grayscale_avg_de"),
                "gamma_midtones": h.get("gamma_midtones"),
                "color_100_avg_de": h.get("color_100_avg_de"),
            }
        )

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
            {
                "role": "system",
                "content": "You are a strict JSON-only responder. Output only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 30.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise ValueError(f"LLM returned empty choices array: {raw[:300]}")
        content = choices[0]["message"]["content"].strip()
        content = _extract_json(content)
        obj = json.loads(content)
        return PatchStrategy(
            focus=str(obj.get("focus", "confirm_only")),
            rationale=str(obj.get("rationale", "")),
            add_patches=list(obj.get("add_patches") or []),
            skip_patches=list(obj.get("skip_patches") or []),
            confidence=float(obj.get("confidence", 0.5)),
        )
    except urllib.error.HTTPError as e:
        logger.error("LLM HTTP error: %s - %s", e.code, e.reason)
        return None
    except json.JSONDecodeError as e:
        logger.error("LLM response parsing failed: %s", e)
        return None
    except Exception as e:
        logger.error(
            "Unexpected LLM error in query_next_patch_strategy: %s: %s",
            type(e).__name__,
            e,
        )
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
            {
                "role": "system",
                "content": "You are a strict JSON-only responder. Output only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 20.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise ValueError(f"LLM returned empty choices array: {raw[:300]}")
        content = choices[0]["message"]["content"].strip()
        content = _extract_json(content)
        obj = json.loads(content)
        return {
            "steps": list(obj.get("steps") or []),
            "explanation": str(obj.get("explanation", "")),
            "confidence": float(obj.get("confidence", 0.5)),
        }
    except urllib.error.HTTPError as e:
        logger.error("LLM HTTP error: %s - %s", e.code, e.reason)
        return None
    except json.JSONDecodeError as e:
        logger.error("LLM response parsing failed: %s", e)
        return None
    except Exception as e:
        logger.error(
            "Unexpected LLM error in query_remediation: %s: %s", type(e).__name__, e
        )
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
            {
                "role": "system",
                "content": "You are a strict calibration assistant. Be concise and technical.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }

    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 30.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise ValueError(f"LLM returned empty choices array: {raw[:300]}")
        return choices[0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        logger.error("LLM HTTP error: %s - %s", e.code, e.reason)
        return None
    except json.JSONDecodeError as e:
        logger.error("LLM response parsing failed: %s", e)
        return None
    except Exception as e:
        logger.error(
            "Unexpected LLM error in query_gamut_advice: %s: %s", type(e).__name__, e
        )
        return None


def probe_llm(cfg: Dict[str, Any], timeout: float = 8.0) -> tuple[bool, str]:
    """POST a minimal completions request to verify endpoint, model, and credentials.

    Returns (reachable, error_detail).  Uses max_tokens=1 to minimise cost/latency.
    Unlike a bare TCP check, this catches invalid model names, wrong API keys, and
    misconfigured proxies that would otherwise only fail at analysis time.
    """
    # Build an LLMConfig so the probe sends the same provider headers
    # (HTTP-Referer, X-Title) as a real call — otherwise the probe can pass
    # while real calls fail (e.g. a bad HTTP-Referer hitting a blocklist).
    # Imported locally: models.py is only a TYPE_CHECKING import at module
    # level, and a top-level import here would create a circular dependency.
    from .models import LLMConfig

    llm = LLMConfig.from_dict(cfg)
    if not llm.endpoint or not llm.model:
        return False, "endpoint and model are required"

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "temperature": 0,
    }
    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            return False, "LLM returned empty choices array"
        _ = choices[0]["message"]["content"]
        return True, ""
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = str(exc)
        # Truncate long error bodies (e.g. HTML error pages)
        return False, f"HTTP {exc.code}: {detail[:300]}"
    except Exception as exc:
        return False, str(exc)[:300]


def query_delta_summary(
    report_a: Dict[str, Any],
    report_b: Dict[str, Any],
    deltas: Dict[str, Any],
    llm: LLMConfig,
) -> Optional[str]:
    """Ask the LLM to narrate the before/after delta between two calibration sessions.

    Returns a plain-language paragraph describing what improved, what regressed,
    and what likely caused the changes. Returns None if LLM is not configured or
    the response cannot be retrieved.
    """
    if not llm.endpoint or not llm.model:
        return None

    def _compact_report(r: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tv": r.get("tv"),
            "mode": r.get("mode"),
            "date": str(r.get("date", ""))[:10],
            "pre_cal_avg_de": r.get("pre_cal", {}).get("avg_de"),
            "post_cal_avg_de": r.get("post_cal", {}).get("avg_de"),
            "wb_avg_de": r.get("white_balance", {}).get("avg_de"),
            "cms_avg_de": r.get("color_tuner", {}).get("avg_de"),
            "gamma_avg": r.get("gamma", {}).get("avg_gamma"),
            "improvement_pct": r.get("improvement_pct"),
            "peak_luminance": r.get("peak_luminance"),
            "target_gamut": r.get("target", {}).get("gamut"),
            "target_eotf": r.get("target", {}).get("eotf"),
        }

    payload = {
        "session_a": _compact_report(report_a),
        "session_b": _compact_report(report_b),
        "deltas": deltas,
    }

    prompt = (
        "You are a professional display calibration analyst. "
        "Two calibration sessions for the same TV are shown below. "
        "Session A is the earlier/baseline; Session B is the more recent.\n\n"
        "Write a single plain-English paragraph (3-5 sentences) that:\n"
        "1. States what improved between sessions (lower ΔE, better gamma, etc.)\n"
        "2. States anything that regressed or stayed roughly the same\n"
        "3. Provides a brief calibration interpretation (e.g. 'The white balance "
        "correction in session B significantly reduced grayscale tracking error')\n\n"
        "Be concise and technical. Do not use JSON — respond with prose only.\n\n"
        f"DATA:\n{json.dumps(payload, indent=2, default=str)}"
    )

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [
            {
                "role": "system",
                "content": "You are a concise display calibration analyst. Write plain prose, no JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }

    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 45.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise ValueError(f"LLM returned empty choices: {raw[:300]}")
        return choices[0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        logger.error("LLM HTTP error in query_delta_summary: %s - %s", e.code, e.reason)
        return None
    except Exception as e:
        logger.error("Unexpected error in query_delta_summary: %s: %s", type(e).__name__, e)
        return None


@dataclass
class PassDecision:
    """LLM-recommended pass decision: accept, repatch, or flag hardware ceiling."""

    action: str  # "accept" | "repatch" | "ceiling"
    patches: List[str]  # patch labels to re-measure (only for "repatch")
    reason: str  # plain-English explanation
    confidence: float  # 0–1
    repass_count: int  # how many repasses already done this phase
    ceiling_reason: Optional[str] = None  # why hardware ceiling was flagged


def query_pass_decision(
    measurements: List[Dict[str, Any]],
    phase: str,
    signal_range: str,
    code_scale: str,
    target_gamma: float,
    target_peak_nits: float,
    target_white_point: List[float],
    target_gamut: str,
    llm: LLMConfig,
    repass_count: int = 0,
) -> Optional[PassDecision]:
    """Ask the LLM to evaluate measurement residuals and decide: accept, repatch, or ceiling.

    Returns a PassDecision dataclass, or None if LLM is not configured or
    the response cannot be parsed.
    """
    if not llm.endpoint or not llm.model:
        return None

    if not measurements:
        return None

    # Compute per-region residual summaries
    grayscale: List[Dict[str, Any]] = []
    colors: Dict[str, List[Dict[str, Any]]] = {}
    for m in measurements:
        label = (m.get("label") or "").strip()
        de = _safe_float(m.get("delta_e"))
        if de is None:
            continue
        stim_pct = m.get("stimulus_pct", 0)
        is_color = any(label.startswith(c) for c in ("Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"))
        if is_color:
            color_name = label.split(" 100%")[0].split(" 100% gray")[0]
            colors.setdefault(color_name, []).append({
                "label": label,
                "dE": round(de, 2),
                "stimulus_pct": stim_pct,
                "x": m.get("x"),
                "y": m.get("y"),
                "Y": m.get("Y"),
            })
        else:
            grayscale.append({
                "label": label,
                "dE": round(de, 2),
                "stimulus_pct": stim_pct,
                "effective_gamma": _safe_float(m.get("effective_gamma")),
                "x": m.get("x"),
                "y": m.get("y"),
                "Y": m.get("Y"),
                "cct": _safe_float(m.get("cct")),
            })

    if not grayscale and not colors:
        return None

    # Compute per-region stats
    def _region_stats(items: List[Dict]) -> Optional[Dict]:
        if not items:
            return None
        des = [i["dE"] for i in items]
        worst = max(items, key=lambda i: i["dE"])
        return {
            "count": len(items),
            "avg_de": round(sum(des) / len(des), 2),
            "max_de": round(max(des), 2),
            "worst_label": worst["label"],
            "worst_de": round(worst["dE"], 2),
        }

    grayscale_stats = _region_stats(grayscale)
    color_stats = {}
    for name, items in colors.items():
        color_stats[name] = _region_stats(items)

    # Gamma deviation stats
    gamma_entries = [g for g in grayscale if g.get("effective_gamma") is not None]
    gamma_deviations = []
    for g in gamma_entries:
        dev = abs(g["effective_gamma"] - target_gamma)
        gamma_deviations.append({
            "label": g["label"],
            "stimulus_pct": g["stimulus_pct"],
            "measured_gamma": round(g["effective_gamma"], 3),
            "target_gamma": target_gamma,
            "deviation": round(dev, 3),
        })

    # CCT deviation stats
    cct_entries = [g for g in grayscale if g.get("cct") is not None]
    cct_devs = []
    for c in cct_entries:
        ref_cct = 6504  # D65
        cct_devs.append({
            "label": c["label"],
            "stimulus_pct": c["stimulus_pct"],
            "measured_cct": round(c["cct"]),
            "deviation_k": round(abs(c["cct"] - ref_cct), 0),
        })

    payload = {
        "phase": phase,
        "repass_count": repass_count,
        "max_repasses": _REPATCH_MAX_PASSES,
        "grayscale": grayscale_stats,
        "colors": color_stats,
        "gamma_deviations": gamma_deviations[:10],  # top 10 gamma entries
        "cct_deviations": cct_devs[:10],
        "target": {
            "gamma": target_gamma,
            "peak_nits": target_peak_nits,
            "white_point": target_white_point,
            "gamut": target_gamut,
        },
    }

    prompt = (
        "You are a TV calibration measurement quality gate. "
        "Evaluate the measurement residuals for the current phase and decide whether to: "
        "(a) accept and advance, (b) trigger targeted re-measurement of the worst patches, "
        "or (c) flag a hardware ceiling (TV cannot achieve target regardless of adjustments).\n\n"
        "Decision rules:\n"
        "- ACCEPT if: grayscale avg ΔE ≤ 2.0 AND max ΔE ≤ 3.0 AND no single color ΔE > 4.0\n"
        "- REPATCH if: avg ΔE > 2.0 OR max ΔE > 3.0 AND repass_count < 3 — list the worst 3-5 patches to re-measure\n"
        "- CEILING if: repass_count ≥ 3 AND worst color ΔE > 5.0, OR grayscale max ΔE > 6.0 AND no improvement trend\n\n"
        "Respond with ONLY a JSON object in this exact schema (no markdown, no extra text):\n"
        "{\n"
        '  "action": "accept" | "repatch" | "ceiling",\n'
        '  "patches": ["<worst patch labels>"],\n'
        '  "reason": "<1-2 sentences explaining the decision>",\n'
        '  "confidence": <0.0-1.0>,\n'
        '  "repass_count": <current repass count>,\n'
        '  "ceiling_reason": "<only if action=ceiling, otherwise null>"\n'
        "}\n\n"
        f"MEASUREMENT DATA:\n{json.dumps(payload, indent=2, default=str)}"
    )

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict JSON-only responder. Output only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 45.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise ValueError(f"LLM returned empty choices: {raw[:300]}")
        content = _extract_json(choices[0]["message"]["content"].strip())
        obj = json.loads(content)

        action = str(obj.get("action", "accept"))
        if action not in ("accept", "repatch", "ceiling"):
            action = "accept"

        return PassDecision(
            action=action,
            patches=list(obj.get("patches") or []),
            reason=str(obj.get("reason", "")),
            confidence=float(obj.get("confidence", 0.5)),
            repass_count=int(obj.get("repass_count", repass_count)),
            ceiling_reason=obj.get("ceiling_reason"),
        )
    except urllib.error.HTTPError as e:
        logger.error("LLM HTTP error in query_pass_decision: %s - %s", e.code, e.reason)
        return None
    except json.JSONDecodeError as e:
        logger.error("LLM response parsing failed in query_pass_decision: %s", e)
        return None
    except Exception as e:
        logger.error(
            "Unexpected error in query_pass_decision: %s: %s", type(e).__name__, e
        )
        return None


def query_patch_optimization(
    grayscale_rows: List[Dict[str, Any]],
    color_rows: List[Dict[str, Any]],
    phase: str,
    patch_budget: int,
    llm: LLMConfig,
    code_max: int = 255,
) -> Optional[Any]:
    """Ask the LLM to recommend an optimized patch set from per-patch residuals.

    Sends full per-patch error data (not just top-N) so the LLM can identify
    stimulus regions that need denser sampling. Returns a structured patch list
    or None if LLM is not configured / response cannot be parsed.
    """
    if not llm.endpoint or not llm.model:
        return None

    from .patch_planner import plan_patches, PatchOptimization, SuggestedPatch

    planning_payload = plan_patches(grayscale_rows, color_rows, budget=patch_budget)

    prompt = (
        "You are a TV calibration measurement strategist. "
        "Given per-patch ΔE residuals from the current calibration sweep, "
        "recommend an optimized patch set for the next measurement pass.\n\n"
        "Rules:\n"
        f"- Total patches must not exceed {patch_budget}\n"
        "- Add denser sampling where ΔE > 3 or gamma deviates > 0.1 from target\n"
        "- Skip patches where ΔE < 1 to save measurement time\n"
        "- For grayscale patches, specify r=g=b values (0-255 for 8-bit, 0-1023 for 10-bit)\n"
        "- For color patches, specify r/g/b independently\n"
        "- Estimate nits from stimulus level and typical display brightness\n\n"
        "Respond with ONLY a JSON object in this exact schema (no markdown, no extra text):\n"
        "{\n"
        '  "patches": [\n'
        "    {\n"
        '      "nits": <estimated_nits>,\n'
        '      "r": <0-1023>,\n'
        '      "g": <0-1023>,\n'
        '      "b": <0-1023>,\n'
        '      "priority": "high" | "medium" | "low",\n'
        '      "label": "<human-readable label>",\n'
        '      "rationale": "<why this patch was added or kept>"\n'
        "    }\n"
        "  ],\n"
        '  "rationale": "<1-2 sentence summary of the optimization strategy>",\n'
        '  "confidence": <0.0-1.0>\n'
        "}\n\n"
        f"PHASE: {phase}\n"
        f"RESIDUALS:\n{json.dumps(planning_payload, indent=2, default=str)}"
    )

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict JSON-only responder. Output only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 60.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise ValueError(f"LLM returned empty choices: {raw[:300]}")
        content = _extract_json(choices[0]["message"]["content"].strip())
        obj = json.loads(content)

        raw_patches = obj.get("patches") or []
        # Cap at budget
        raw_patches = raw_patches[:patch_budget]
        patches = [SuggestedPatch.from_dict(p, code_max=code_max) for p in raw_patches]
        confidence = float(obj.get("confidence", 0.5))

        return PatchOptimization(
            patches=patches,
            rationale=str(obj.get("rationale", "")),
            confidence=confidence,
            auto_apply=confidence >= 0.7,
        )
    except urllib.error.HTTPError as e:
        logger.error("LLM HTTP error in query_patch_optimization: %s - %s", e.code, e.reason)
        return None
    except json.JSONDecodeError as e:
        logger.error("LLM response parsing failed in query_patch_optimization: %s", e)
        return None
    except Exception as e:
        logger.error(
            "Unexpected error in query_patch_optimization: %s: %s", type(e).__name__, e
        )
        return None


# Type alias for callers that import PatchOptimization from here
_resolve_endpoint = resolve_endpoint
call_local_llm = call_llm


@dataclass
class PredictedSettings:
    """LLM-predicted starting settings for a step, derived from prior-session history."""

    settings: List[Dict[str, Any]]  # each: {menu, setting, value, scope, reason}
    confidence: float  # 0.0–1.0
    source: str  # "history" | "cold_start"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "settings": self.settings,
            "confidence": self.confidence,
            "source": self.source,
        }


def predict_initial_settings(
    phase: str,
    history: List[Dict[str, Any]],
    baseline: Optional[Dict[str, Any]],
    tv_schema: Optional[Dict[str, Any]],
    llm: LLMConfig,
) -> Optional[PredictedSettings]:
    """Ask the LLM to predict good starting hardware settings for a calibration step.

    Uses prior-session history (wb_final/cms_final) and the TV settings schema to
    produce a warm-start recommendation so the user begins close to optimal.

    Returns None if LLM is not configured or the response cannot be parsed.
    Returns PredictedSettings with source="cold_start" when history is empty and
    baseline is None (no network call).
    """
    if not llm.endpoint or not llm.model:
        return None

    # Cold start — no prior data, no network call needed
    if not history and baseline is None:
        return PredictedSettings(settings=[], confidence=0.0, source="cold_start")

    history_block = build_history_block(history, baseline)
    schema_str = json.dumps(tv_schema, indent=2, default=str) if tv_schema else "(none)"

    prompt = (
        "You are a TV calibration expert. Based on this display's PRIOR calibration\n"
        "sessions, predict good STARTING hardware settings for the \""
        f"{phase}\" step so the\nuser begins close to optimal and needs fewer "
        "measurement rounds.\n\n"
        "Use the prior sessions' final applied settings (wb_final/cms_final) and the TV\n"
        "settings schema (valid menus, settings, and value ranges). Stay within the\n"
        "schema ranges. If prior data is weak or absent, return an empty settings list\n"
        "with low confidence.\n\n"
        "Respond with ONLY a JSON object in this exact schema (no markdown, no prose):\n"
        "{\n"
        '  "settings": [\n'
        '    {"menu": "<menu>", "setting": "<setting>", "value": <number>,\n'
        '     "scope": "global" | "local", "reason": "<1 sentence>"}\n'
        "  ],\n"
        '  "confidence": <0.0-1.0>\n'
        "}\n\n"
        f"PHASE: {phase}\n"
        f"PRIOR SESSIONS:\n{history_block}\n"
        f"TV SETTINGS SCHEMA:\n{schema_str}"
    )

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict JSON-only responder. Output only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 45.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise ValueError(f"LLM returned empty choices array: {raw[:300]}")
        content = _extract_json(choices[0]["message"]["content"].strip())
        obj = json.loads(content)

        raw_settings = obj.get("settings") or []
        settings = [dict(s) for s in raw_settings]
        confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.5))))

        return PredictedSettings(
            settings=settings,
            confidence=confidence,
            source="history",
        )
    except urllib.error.HTTPError as e:
        logger.error(
            "LLM HTTP error in predict_initial_settings: %s - %s", e.code, e.reason
        )
        return None
    except json.JSONDecodeError as e:
        logger.error("LLM response parsing failed in predict_initial_settings: %s", e)
        return None
    except Exception as e:
        logger.error(
            "Unexpected error in predict_initial_settings: %s: %s", type(e).__name__, e
        )
        return None


# ── Convergence-aware reactive next-settings prediction (#337) ─────────────────
#
# Design decisions (resolving the open questions in the issue):
#
#   1. "Converged" reuses the existing quality-gate constants. We pull per-phase
#      thresholds from the TV profile's ``quality_gate_thresholds`` and fall back
#      to ``_DEFAULT_CONVERGENCE_THRESHOLDS`` (which mirror the U8G profile and the
#      ACCEPT rule baked into ``query_pass_decision``: grayscale avg ΔE ≤ 2.0,
#      max ≤ 3.0). No new per-phase magic numbers are invented.
#
#   2. Overshoot/damping is fed to the model as explicit prior (suggested-delta →
#      resulting-residual) pairs plus a trust-region instruction: if the last
#      delta failed to reduce the residual (or flipped its sign), apply a damped
#      fraction of the remaining correction rather than repeating the same delta.
#      Rounds are tracked deterministically by the caller and capped here.
#
#   3. Auto-advance vs. confirm: when within tolerance we DO NOT mutate hardware.
#      We surface a high-confidence ``next_step="verify"`` so the user accepts the
#      proceed. The convergence short-circuit makes no network call.
#
#   4. Round cap + stall: reuse ``_REPATCH_MAX_PASSES``. When the cap is reached
#      OR round-over-round improvement falls below ``_CONVERGENCE_STALL_EPSILON``
#      while still out of tolerance, we emit a "diminishing returns / hardware
#      ceiling" prediction (``next_step="ceiling"``) without a network call.

# Round-over-round avg ΔE improvement below this counts as a stall (diminishing
# returns). 0.3 ΔE is below the ~1.0 ΔE just-noticeable threshold, so further
# rounds are not worth the measurement cost.
_CONVERGENCE_STALL_EPSILON = 0.3

# Per-phase convergence thresholds used when the TV profile omits them. These
# mirror the Hisense U8G ``quality_gate_thresholds`` and the ACCEPT decision rule
# in ``query_pass_decision`` so the reactive loop and the quality gate agree.
_DEFAULT_CONVERGENCE_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "grayscale": {"avg_de": 2.0, "max_de": 3.0},
    "white_balance": {"avg_de": 1.5, "max_de": 2.5},
    "color_tuner": {"avg_de": 3.0, "max_de": 4.0},
    "gamma": {"max_deviation": 0.05},
}


def _phase_threshold_key(phase: str) -> str:
    """Map a calibration phase string to a ``quality_gate_thresholds`` key.

    Phases seen in the codebase: ``pre_grayscale``, ``post_grayscale``,
    ``white_balance``, ``gamma``, ``color_tuner``, ``baseline``. Grayscale is the
    default so baseline/pre/post all gate on grayscale tracking.
    """
    p = (phase or "").lower()
    if "color" in p or "cms" in p:
        return "color_tuner"
    if "gamma" in p:
        return "gamma"
    if "white" in p or "balance" in p or p == "wb":
        return "white_balance"
    return "grayscale"


@dataclass
class ConvergenceAssessment:
    """Deterministic convergence verdict for the current measurement round."""

    converged: bool
    stalled: bool
    rounds_used: int
    rounds_remaining: int
    metric_key: str  # which threshold group applied
    avg_de: Optional[float]
    max_de: Optional[float]
    gamma_deviation: Optional[float]
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "converged": self.converged,
            "stalled": self.stalled,
            "rounds_used": self.rounds_used,
            "rounds_remaining": self.rounds_remaining,
            "metric_key": self.metric_key,
            "avg_de": self.avg_de,
            "max_de": self.max_de,
            "gamma_deviation": self.gamma_deviation,
            "detail": self.detail,
        }


def assess_convergence(
    summary: "Summary",
    phase: str,
    thresholds: Optional[Dict[str, Any]] = None,
    rounds_used: int = 0,
    round_cap: int = _REPATCH_MAX_PASSES,
    prev_avg_de: Optional[float] = None,
    target_gamma: Optional[float] = None,
) -> ConvergenceAssessment:
    """Decide — deterministically, no LLM — whether the current phase has converged.

    ``prev_avg_de`` is the residual avg ΔE recorded one round earlier; when the
    improvement since then is below ``_CONVERGENCE_STALL_EPSILON`` and we are
    still out of tolerance, the phase is flagged ``stalled`` (diminishing
    returns). ``rounds_remaining`` is the budget left before the round cap.
    """
    key = _phase_threshold_key(phase)
    thr = (thresholds or {}).get(key) or _DEFAULT_CONVERGENCE_THRESHOLDS.get(key, {})

    if key == "color_tuner":
        avg_de = (
            summary.color_100_avg_de
            if summary.color_100_avg_de is not None
            else summary.color_75_avg_de
        )
        max_de = (
            summary.color_100_max_de
            if summary.color_100_max_de is not None
            else summary.color_75_max_de
        )
    else:
        avg_de = summary.grayscale_avg_de
        max_de = summary.grayscale_max_de

    gamma_deviation: Optional[float] = None
    if target_gamma is not None and summary.gamma_midtones is not None:
        gamma_deviation = abs(summary.gamma_midtones - target_gamma)

    avg_thr = thr.get("avg_de")
    max_thr = thr.get("max_de")
    gamma_thr = thr.get("max_deviation")

    has_data = any(v is not None for v in (avg_de, max_de, gamma_deviation))
    within = True
    if avg_thr is not None:
        within = within and (avg_de is not None and avg_de <= avg_thr)
    if max_thr is not None:
        within = within and (max_de is not None and max_de <= max_thr)
    # Gamma is only a convergence gate during the gamma phase.
    if key == "gamma" and gamma_thr is not None:
        within = within and (gamma_deviation is not None and gamma_deviation <= gamma_thr)

    converged = bool(within and has_data)

    rounds_remaining = max(0, round_cap - rounds_used)

    stalled = False
    if (
        not converged
        and prev_avg_de is not None
        and avg_de is not None
        and rounds_used >= 1
        and (prev_avg_de - avg_de) < _CONVERGENCE_STALL_EPSILON
    ):
        stalled = True

    if converged:
        detail = (
            f"{key} within tolerance (avg ΔE={avg_de}, max ΔE={max_de}); proceed."
        )
    elif not has_data:
        detail = f"No {key} residual data available to assess convergence."
    elif stalled:
        detail = (
            f"{key} stalled at avg ΔE={avg_de} (prev {prev_avg_de}); "
            f"improvement < {_CONVERGENCE_STALL_EPSILON} ΔE — diminishing returns."
        )
    else:
        detail = (
            f"{key} out of tolerance (avg ΔE={avg_de}, max ΔE={max_de}); "
            f"{rounds_remaining} round(s) remaining."
        )

    return ConvergenceAssessment(
        converged=converged,
        stalled=stalled,
        rounds_used=rounds_used,
        rounds_remaining=rounds_remaining,
        metric_key=key,
        avg_de=round(avg_de, 2) if isinstance(avg_de, (int, float)) else avg_de,
        max_de=round(max_de, 2) if isinstance(max_de, (int, float)) else max_de,
        gamma_deviation=round(gamma_deviation, 3)
        if isinstance(gamma_deviation, (int, float))
        else gamma_deviation,
        detail=detail,
    )


@dataclass
class NextSettingsPrediction:
    """Convergence-aware next-round prediction for the reactive settings loop."""

    adjustments: List[Dict[str, Any]]  # same shape as AdjustmentPlan.adjustments
    # "rerun_grayscale" | "rerun_wb" | "proceed_cms" | "verify" | "ceiling"
    next_step: str
    confidence: float
    converged: bool
    stalled: bool
    rounds_used: int
    rounds_remaining: int
    convergence: Dict[str, Any]  # ConvergenceAssessment.to_dict()
    message: str
    source: str  # "converged" | "ceiling" | "llm"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adjustments": self.adjustments,
            "next_step": self.next_step,
            "confidence": self.confidence,
            "converged": self.converged,
            "stalled": self.stalled,
            "rounds_used": self.rounds_used,
            "rounds_remaining": self.rounds_remaining,
            "convergence": self.convergence,
            "message": self.message,
            "source": self.source,
        }


def _format_adjustment_rounds(prior_rounds: List[Dict[str, Any]]) -> str:
    """Render prior (suggested-delta → resulting-residual) pairs for the prompt."""
    lines: List[str] = []
    for r in prior_rounds[-3:]:
        res = r.get("residual") or {}
        suggested = r.get("suggested") or []
        sug_str = (
            "; ".join(
                f"{a.get('menu', '?')}/{a.get('setting', '?')}→{a.get('to')}"
                for a in suggested
            )
            or "(none)"
        )
        lines.append(
            f"Round {r.get('round')}: observed avg ΔE={res.get('avg_de')}, "
            f"max ΔE={res.get('max_de')}"
            + (
                f", gamma={res.get('gamma')}"
                if res.get("gamma") is not None
                else ""
            )
            + f" → suggested {sug_str}"
        )
    return "\n".join(lines)


def _apply_round1_damping(
    adjustments: List[Dict[str, Any]], rounds_used: int
) -> List[Dict[str, Any]]:
    """Clamp Round-1 deltas to ``_ROUND1_DAMPING_FACTOR`` of the LLM's suggested
    correction, deterministically enforcing the "conservative Round-1
    correction factor (~0.55) for inter-node bleed" invariant regardless of
    whether the model actually complied with the prompt's damping instruction.

    Only the first round is damped here (there is no prior residual to trend
    against yet). Later rounds rely on the prior-rounds history fed back into
    the prompt, which already tells the model what did and didn't work.

    A numeric ``to`` with a null ``from`` has no baseline to damp against —
    the schema permits ``"from": null`` for cases where the model doesn't know
    the current value. Passing such an adjustment through as-is on round 1
    would apply the full, undamped absolute value and bypass the clamp
    entirely (#579), so it is dropped rather than auto-applied undamped.
    Non-numeric targets (e.g. an enum/preset like "Movie") aren't a damping
    concern at all and pass through unchanged.
    """
    if rounds_used != 0:
        return adjustments

    damped: List[Dict[str, Any]] = []
    for adj in adjustments:
        from_val = _safe_float(adj.get("from"))
        to_val = _safe_float(adj.get("to"))
        if to_val is None:
            damped.append(adj)
            continue
        if from_val is None:
            logger.warning(
                "_apply_round1_damping: dropping round-1 adjustment for %s/%s "
                "— numeric 'to' with null 'from' has no baseline to damp against",
                adj.get("menu"), adj.get("setting"),
            )
            continue
        if from_val == to_val:
            damped.append(adj)
            continue
        new_to = from_val + _ROUND1_DAMPING_FACTOR * (to_val - from_val)
        if isinstance(adj.get("from"), int) and isinstance(adj.get("to"), int):
            new_to = round(new_to)
        else:
            new_to = round(new_to, 4)
        damped.append({**adj, "to": new_to})
    return damped


def predict_next_settings(
    summary: "Summary",
    cfg: "AnalysisConfig",
    phase: str,
    llm: "LLMConfig",
    prior_rounds: Optional[List[Dict[str, Any]]] = None,
    thresholds: Optional[Dict[str, Any]] = None,
    round_cap: int = _REPATCH_MAX_PASSES,
    tv_settings: Optional["TVSettings"] = None,
    tv_schema: Optional[Dict[str, Any]] = None,
    target_gamma: Optional[float] = None,
) -> Optional[NextSettingsPrediction]:
    """Predict the next round of settings, aware of convergence trend and round cap.

    Unlike ``call_llm`` (single-shot, stateless), this folds in prior adjustment
    rounds and short-circuits — with no network call — when the phase has either
    converged (``next_step="verify"``) or hit the round cap / stalled
    (``next_step="ceiling"``). Otherwise it asks the LLM for the next deltas,
    feeding it the suggested-vs-resulting residual history and a damping
    instruction so it corrects rather than repeating an overshooting delta.
    """
    if not llm.endpoint or not llm.model:
        return None

    prior_rounds = prior_rounds or []
    rounds_used = len(prior_rounds)
    prev_avg_de: Optional[float] = None
    if prior_rounds:
        last_res = prior_rounds[-1].get("residual") or {}
        prev_avg_de = last_res.get("avg_de")

    assessment = assess_convergence(
        summary,
        phase,
        thresholds=thresholds,
        rounds_used=rounds_used,
        round_cap=round_cap,
        prev_avg_de=prev_avg_de,
        target_gamma=target_gamma,
    )

    # Short-circuit 1 — converged: surface a high-confidence proceed, no network.
    if assessment.converged:
        return NextSettingsPrediction(
            adjustments=[],
            next_step="verify",
            confidence=0.95,
            converged=True,
            stalled=False,
            rounds_used=rounds_used,
            rounds_remaining=assessment.rounds_remaining,
            convergence=assessment.to_dict(),
            message=(
                f"Within tolerance after {rounds_used} round(s) — "
                "no further adjustment needed. Proceed to verify."
            ),
            source="converged",
        )

    # Short-circuit 2 — round cap reached or stalled: diminishing-returns ceiling.
    if rounds_used >= round_cap or assessment.stalled:
        if rounds_used >= round_cap:
            reason = (
                f"Round cap ({round_cap}) reached without converging "
                f"(avg ΔE={assessment.avg_de})."
            )
        else:
            reason = (
                f"Diminishing returns — avg ΔE stalled at {assessment.avg_de} "
                f"(prev {prev_avg_de}). Likely hardware ceiling."
            )
        return NextSettingsPrediction(
            adjustments=[],
            next_step="ceiling",
            confidence=0.6,
            converged=False,
            stalled=assessment.stalled,
            rounds_used=rounds_used,
            rounds_remaining=0,
            convergence=assessment.to_dict(),
            message=reason,
            source="ceiling",
        )

    # Otherwise ask the LLM for the next round, with damping context.
    summary_dict = {
        k: v
        for k, v in asdict(summary).items()
        if k not in ("grayscale_rows", "color_rows")
    }
    top = _top_offenders(summary)
    if top:
        summary_dict["top_offenders"] = top

    payload: Dict[str, Any] = {
        "phase": phase,
        "mode": cfg.mode,
        "eotf": cfg.eotf,
        "target_space": cfg.target_space,
        "round": rounds_used + 1,
        "rounds_remaining": assessment.rounds_remaining,
        "convergence_targets": (thresholds or {}).get(assessment.metric_key)
        or _DEFAULT_CONVERGENCE_THRESHOLDS.get(assessment.metric_key, {}),
        "current_residual": {
            "avg_de": assessment.avg_de,
            "max_de": assessment.max_de,
            "gamma_deviation": assessment.gamma_deviation,
        },
        "summary": summary_dict,
    }
    if tv_settings is not None:
        ts_dict = {k: v for k, v in asdict(tv_settings).items() if v is not None}
        if ts_dict:
            payload["current_tv_settings"] = ts_dict
    if tv_schema:
        payload["tv_settings_schema"] = tv_schema

    rounds_block = _format_adjustment_rounds(prior_rounds) or "(none yet)"

    prompt = (
        "You are converging a TV calibration loop in as few rounds as possible. "
        f"This is round {rounds_used + 1} of at most {round_cap}. Predict the NEXT "
        "hardware deltas so the residual drops below the convergence targets.\n\n"
        "Damping / trust-region rules:\n"
        "- Review the PRIOR ROUNDS below: each shows the residual you were "
        "correcting and the delta you suggested.\n"
        "- If a prior delta did NOT reduce the residual (or made it worse), do not "
        "repeat it — apply a DAMPED fraction (about half) of the remaining "
        "correction in the same direction, or reverse if you overshot.\n"
        "- Prefer the smallest delta that reaches tolerance; avoid oscillation.\n"
        "- If you are already within the convergence targets, return an empty "
        '"adjustments" array with "next_step": "verify" and high confidence.\n\n'
        "Respond with ONLY a valid JSON object matching this exact schema "
        "(no markdown, no prose outside the JSON):\n"
        "{\n"
        '  "adjustments": [\n'
        "    {\n"
        '      "menu": "<TV menu name>",\n'
        '      "setting": "<setting name>",\n'
        '      "from": <current value or null>,\n'
        '      "to": <target value>,\n'
        '      "scope": "global" | "local",\n'
        '      "reason": "<1-2 sentences, physics-based>"\n'
        "    }\n"
        "  ],\n"
        '  "next_step": "rerun_grayscale" | "rerun_wb" | "proceed_cms" | "verify",\n'
        '  "confidence": <0.0-1.0>\n'
        "}\n\n"
        f"PRIOR ROUNDS (suggested → resulting residual):\n{rounds_block}\n\n"
        f"PAYLOAD:\n{json.dumps(payload, indent=2, default=str)}"
    )

    url = resolve_endpoint(llm.endpoint)
    body = {
        "model": llm.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    req = _build_request(url, body, llm)

    try:
        with urllib.request.urlopen(req, timeout=min(llm.timeout, 45.0)) as resp:
            raw = resp.read().decode("utf-8")
        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        if not choices:
            raise ValueError(f"LLM returned empty choices array: {raw[:300]}")
        content = choices[0]["message"]["content"].strip()
        plan = parse_adjustment_plan(content)
        if plan is None:
            logger.warning(
                "predict_next_settings: could not parse adjustment plan from: %s",
                content[:500],
            )
            return None

        return NextSettingsPrediction(
            adjustments=_apply_round1_damping(plan.adjustments, rounds_used),
            next_step=plan.next_step,
            confidence=plan.confidence,
            converged=False,
            stalled=False,
            rounds_used=rounds_used,
            rounds_remaining=assessment.rounds_remaining,
            convergence=assessment.to_dict(),
            message=assessment.detail,
            source="llm",
        )
    except urllib.error.HTTPError as e:
        logger.error(
            "LLM HTTP error in predict_next_settings: %s - %s", e.code, e.reason
        )
        return None
    except json.JSONDecodeError as e:
        logger.error("LLM response parsing failed in predict_next_settings: %s", e)
        return None
    except Exception as e:
        logger.error(
            "Unexpected error in predict_next_settings: %s: %s", type(e).__name__, e
        )
        return None
