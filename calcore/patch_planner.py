"""Predictive patch density planning from LLM residual analysis.

Analyzes post-grayscale or post-CMS residuals and recommends a custom
next-round patch set: denser sampling where ΔE or gamma deviation is highest,
sparser where results are already within tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MAX_PATCH_BUDGET: int = 30  # configurable default cap on recommended patches
MAX_RGB: int = 255  # 8-bit display maximum for patch RGB values

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class SuggestedPatch:
    """A single patch recommended by the LLM-driven patch optimizer."""

    nits: float
    r: int
    g: int
    b: int
    priority: str  # "high" | "medium" | "low"
    label: str = ""
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nits": self.nits,
            "r": self.r,
            "g": self.g,
            "b": self.b,
            "priority": self.priority,
            "label": self.label,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any], code_max: int = MAX_RGB) -> "SuggestedPatch":
        r = max(0, min(int(d.get("r", 0)), code_max))
        g = max(0, min(int(d.get("g", 0)), code_max))
        b = max(0, min(int(d.get("b", 0)), code_max))
        return cls(
            nits=float(d.get("nits", 0.0)),
            r=r,
            g=g,
            b=b,
            priority=str(d.get("priority", "medium")),
            label=str(d.get("label", "")),
            rationale=str(d.get("rationale", "")),
        )


@dataclass
class PatchOptimization:
    """LLM-generated optimized patch list derived from measurement residuals."""

    patches: List[SuggestedPatch] = field(default_factory=list)
    rationale: str = ""  # plain-English summary of the optimization strategy
    confidence: float = 0.5  # 0.0–1.0
    auto_apply: bool = False  # True when confidence >= 0.7

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patches": [p.to_dict() for p in self.patches],
            "rationale": self.rationale,
            "confidence": self.confidence,
            "auto_apply": self.auto_apply,
            "patch_count": len(self.patches),
        }


def _extract_residuals(
    grayscale_rows: List[Dict[str, Any]],
    color_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Distil per-patch residuals into a compact LLM-friendly summary.

    Includes all patches sorted by ΔE descending so the LLM can clearly see
    where the worst errors are and recommend denser sampling.
    """
    gray_sorted = sorted(
        grayscale_rows,
        key=lambda r: r.get("dE2000") or 0,
        reverse=True,
    )
    color_sorted = sorted(
        color_rows,
        key=lambda r: r.get("dE2000") or 0,
        reverse=True,
    )

    def _row_summary(r: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"label": r.get("label", "")}
        if r.get("dE2000") is not None:
            out["dE2000"] = round(r["dE2000"], 2)
        if r.get("gamma") is not None:
            out["gamma"] = round(r["gamma"], 3)
        if r.get("pq_error_pct") is not None:
            out["pq_error_pct"] = round(r["pq_error_pct"], 1)
        if r.get("dE2000_chroma_only") is not None:
            out["dE2000_chroma"] = round(r["dE2000_chroma_only"], 2)
        return out

    return {
        "grayscale_by_error": [_row_summary(r) for r in gray_sorted],
        "color_by_error": [_row_summary(r) for r in color_sorted[:20]],
    }


def plan_patches(
    grayscale_rows: List[Dict[str, Any]],
    color_rows: List[Dict[str, Any]],
    budget: int = MAX_PATCH_BUDGET,
) -> Dict[str, Any]:
    """Build a compact residual summary to pass to the LLM for patch planning.

    Returns a dict ready to embed in an LLM prompt payload.
    """
    residuals = _extract_residuals(grayscale_rows, color_rows)
    return {
        "residuals": residuals,
        "patch_budget": budget,
    }
