from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict
from typing import Optional

from .models import AnalysisConfig, LLMConfig, Summary


def build_llm_prompt(summary: Summary, cfg: AnalysisConfig, phase: str) -> str:
    payload = {
        "phase": phase,
        "mode": cfg.mode,
        "eotf": cfg.eotf,
        "target_space": cfg.target_space,
        "summary": asdict(summary),
    }
    return (
        "You are a TV calibration co-pilot. Use the provided summary only. "
        "Do not do new math. Do not invent missing data. "
        "Return exactly one calibration step in this format:\n\n"
        "> **Step [Phase.Step]:** [Action title]\n"
        "> **Do this:** [Specific instruction]\n"
        "> **Why:** [1-2 sentences]\n"
        "> **Send me:** [What to send back]\n\n"
        "If the data is insufficient, say exactly what is missing and stop.\n\n"
        f"SUMMARY JSON:\n{json.dumps(payload, indent=2, default=str)}"
    )


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
) -> Optional[str]:
    """Call an OpenAI-compatible chat/completions endpoint (e.g. LiteLLM proxy)."""
    if not llm.endpoint or not llm.model:
        return None

    url = resolve_endpoint(llm.endpoint)
    prompt = build_llm_prompt(summary, cfg, phase)
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


_resolve_endpoint = resolve_endpoint
call_local_llm = call_llm
