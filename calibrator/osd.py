"""
OSD Command Translator - deterministic menu-path resolution for TV calibration corrections.

Maps LLM-generated adjustment vectors (e.g. red_gain += 3, gamma_curve = 2.28) to
step-by-step OSD navigation instructions using the TV profile's llm_schema and menu paths.

For known TV profiles: fully deterministic, no LLM call needed.
For unknown TVs: returns empty list (caller can fall back to LLM-driven approach).

Architecture
------------
Each TV profile's llm_schema contains a "settings" dict keyed by setting name. Each
setting entry has:

  - path: string - OSD menu navigation (arrow-separated)
  - type: "slider" | "enum" | "toggle" | "multipoint" | "cms" | "2pt_wb"
  - channels / colors / params - sub-controls for complex settings
  - range / options / increment - value constraints
  - calibration_target / calibration_default - recommended values

The translator walks the adjustment vector, matches each correction to a schema entry,
and emits an ordered list of OSD navigation steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class OSDStep:
    """A single OSD navigation instruction for the user."""

    step_number: int
    menu_path: str  # e.g. "Settings -> Picture -> White Balance -> IRE 80"
    action: str  # e.g. "Adjust R-Gain", "Select BT.1886", "Set Saturation to 3"
    value: Optional[str] = None  # target value or delta (e.g. "+3", "2.28", "Off")
    rationale: str = ""  # brief explanation for the user
    completed: bool = False  # frontend checkbox state

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_number": self.step_number,
            "menu_path": self.menu_path,
            "action": self.action,
            "value": self.value,
            "rationale": self.rationale,
            "completed": self.completed,
        }


@dataclass
class OSDTranslationResult:
    """Result of translating a correction vector into OSD steps."""

    tv_model: str  # display name, e.g. "Hisense U8G"
    tv_key: str  # profile key, e.g. "u8g"
    steps: List[OSDStep] = field(default_factory=list)
    skipped_reasons: List[str] = field(default_factory=list)  # why some corrections were skipped
    uses_adb: bool = False  # True if ADB can apply corrections directly (skip OSD)
    adb_instructions: Optional[str] = None  # if uses_adb, the ADB commands

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tv_model": self.tv_model,
            "tv_key": self.tv_key,
            "steps": [s.to_dict() for s in self.steps],
            "skipped_reasons": self.skipped_reasons,
            "uses_adb": self.uses_adb,
            "adb_instructions": self.adb_instructions,
        }


# ---------------------------------------------------------------------------
# Adjustment normalisation helpers
# ---------------------------------------------------------------------------


def _normalise_adjustment(adj: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise an adjustment dict from the LLM into a canonical form.

    Accepts both the old AdjustmentPlan format (menu, setting, from, to) and
    the raw correction vector format (setting_name, delta, target_value).
    """
    # Already canonical?
    if "setting_key" in adj:
        return adj

    setting_name = adj.get("setting", "")
    menu = adj.get("menu", "")
    from_val = adj.get("from")
    to_val = adj.get("to")
    scope = adj.get("scope", "local")
    reason = adj.get("reason", "")

    # Try to derive a setting_key from the setting name and menu path
    setting_key = _derive_setting_key(setting_name, menu)

    return {
        "setting_key": setting_key,
        "setting_name": setting_name,
        "menu": menu,
        "from": from_val,
        "to": to_val,
        "delta": _compute_delta(from_val, to_val),
        "scope": scope,
        "reason": reason,
    }


def _derive_setting_key(setting_name: str, menu: str) -> str:
    """Derive a canonical setting_key from the LLM's setting name and menu.

    Maps human-readable names to schema keys used in llm_schema.settings.
    """
    key_map = {
        # White balance channels
        "R-Gain": "white_balance_2pt.channels.R_gain",
        "G-Gain": "white_balance_2pt.channels.G_gain",
        "B-Gain": "white_balance_2pt.channels.B_gain",
        "R-Offset": "white_balance_2pt.channels.R_offset",
        "G-Offset": "white_balance_2pt.channels.G_offset",
        "B-Offset": "white_balance_2pt.channels.B_offset",
        "Red Gain": "white_balance_2pt.channels.R_gain",
        "Green Gain": "white_balance_2pt.channels.G_gain",
        "Blue Gain": "white_balance_2pt.channels.B_gain",
        "Red Offset": "white_balance_2pt.channels.R_offset",
        "Green Offset": "white_balance_2pt.channels.G_offset",
        "Blue Offset": "white_balance_2pt.channels.B_offset",
        # CMS channels
        "Hue": "cms.params.Hue",
        "Saturation": "cms.params.Saturation",
        "Brightness": "cms.params.Brightness",
        "Luminance": "cms.params.Luminance",
        # Gamma
        "Gamma": "gamma",
        "gamma": "gamma",
        # Picture controls
        "Backlight": "backlight",
        "Backlight Level": "backlight",
        "OLED Light": "backlight",
        "Black Level": "black_level",
        "Contrast": "contrast",
        "Colour Temperature": "color_temperature",
        "Color Temperature": "color_temperature",
        "Colour Space": "color_space",
        "Color Space": "color_space",
    }
    return key_map.get(setting_name, setting_name.lower().replace(" ", "_").replace("-", "_"))


def _compute_delta(from_val, to_val) -> Optional[float]:
    """Compute the numeric delta between from and to values."""
    if from_val is None or to_val is None:
        return None
    try:
        return float(to_val) - float(from_val)
    except (TypeError, ValueError):
        return None


def _lookup_nested(d: Dict[str, Any], dotted_key: str) -> Optional[Any]:
    """Look up a dotted key in a nested dict (e.g. 'channels.R_gain').

    Also checks inside d['settings'] for keys like 'white_balance_2pt'.
    """
    keys = dotted_key.split(".")
    current = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    # If the direct lookup failed and d has a 'settings' dict, try there
    if current is None and isinstance(d, dict) and "settings" in d:
        settings = d["settings"]
        current = settings.get(dotted_key)
        if current is not None:
            return current
        # Try nested lookup inside settings (e.g. channels.R_gain)
        nested_keys = dotted_key.split(".")
        for key in nested_keys:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
    return current


def _get_settings_from_profile(tv_profile: Any) -> Dict[str, Any]:
    """Extract the settings dict from a TV profile's llm_schema."""
    if hasattr(tv_profile, "llm_schema") and isinstance(tv_profile.llm_schema, dict):
        return tv_profile.llm_schema.get("settings", {})
    return {}


def _format_value(val: Any, setting_type: str) -> Optional[str]:
    """Format a raw value into a user-friendly display string."""
    if val is None:
        return None
    try:
        f = float(val)
        if setting_type == "enum":
            return str(val)  # keep enum labels as-is
        if abs(f) == int(f):
            return str(int(f))
        return f"{f:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(val)


def _format_delta(delta: float, setting_type: str) -> str:
    """Format a numeric delta for display."""
    if abs(delta) == int(abs(delta)):
        sign = "+" if delta > 0 else "-"
        return f"{sign}{int(abs(delta))}"
    sign = "+" if delta > 0 else "-"
    return f"{sign}{abs(delta):.2f}".rstrip("0").rstrip(".")


def _build_menu_path(base_path: str, channel_name: Optional[str] = None) -> str:
    """Build a user-friendly menu path with -> separators."""
    if channel_name:
        return f"{base_path} -> {channel_name}"
    # Normalise arrow separators
    return base_path.replace(">", "").replace("> ", " ")


def _channel_label_for_key(schema_key: str, schema_entry: Dict) -> Optional[str]:
    """Extract the display label for a channel within a multipoint setting."""
    if "channels" in schema_entry:
        parts = schema_key.split(".")
        if len(parts) >= 2:
            channel_part = parts[-1]
            channels = schema_entry["channels"]
            for ck, cv in channels.items():
                if ck == channel_part:
                    return cv.get("label", ck)
    return None


def _extract_cms_colour(adj: Dict[str, Any]) -> str:
    """Extract the CMS colour name from an adjustment dict."""
    if "colour" in adj:
        return str(adj["colour"])
    if "color" in adj:
        return str(adj["color"])
    menu = adj.get("menu", "")
    setting = adj.get("setting_name", adj.get("setting", ""))
    for colour in ["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"]:
        if colour.lower() in menu.lower() or colour.lower() in setting.lower():
            return colour
    return "Red"  # default fallback


def _build_cms_menu_path(base_path: str, colour_name: Optional[str], param_name: Optional[str]) -> str:
    """Build CMS-specific menu path with colour and parameter."""
    parts = [base_path]
    if colour_name:
        parts.append(colour_name)
    if param_name and param_name not in (colour_name or ""):
        parts.append(param_name)
    return " -> ".join(parts).replace(">", "").replace("> ", " ")


def _generate_wb_steps(tv_profile: Any, adj: Dict[str, Any], step_start: int = 1) -> List[OSDStep]:
    """Generate white balance OSD steps from an adjustment."""
    setting_key = adj.get("setting_key", "")
    channel_part = setting_key.split(".")[-1] if "." in setting_key else setting_key
    channel_label = {
        "R_gain": "Red",
        "G_gain": "Green",
        "B_gain": "Blue",
        "R_offset": "Red",
        "G_offset": "Green",
        "B_offset": "Blue",
    }.get(channel_part, channel_part)

    is_gain = "gain" in channel_part.lower() and "offset" not in channel_part.lower()
    is_offset = "offset" in channel_part.lower()

    wb_path = tv_profile.WB_MENU_PATH if hasattr(tv_profile, "WB_MENU_PATH") else ""
    if not wb_path:
        return []

    value_label = None
    if adj.get("to") is not None:
        value_label = _format_value(adj["to"], "slider")
    elif adj.get("delta") is not None:
        value_label = _format_delta(adj["delta"], "slider")

    rationale = adj.get("reason", "") or (
        f"{'Gain' if is_gain else 'Offset'} adjustment for {channel_label} channel"
    )

    # Check if TV has 2-point WB with IRE points
    wb_schema = _lookup_nested(tv_profile.llm_schema, "white_balance_2pt")
    has_2pt = wb_schema and (wb_schema.get("type") == "slider" or "channels" in wb_schema)

    if has_2pt and (is_gain or is_offset):
        ire_label = "IRE 80" if is_gain else "IRE 20"
        menu_path = _build_menu_path(wb_path, f"{ire_label} -> {channel_label}")
        return [OSDStep(
            step_number=step_start,
            menu_path=menu_path,
            action=f"Adjust {channel_label}",
            value=value_label,
            rationale=rationale,
        )]

    # Fallback: single step
    menu_path = _build_menu_path(wb_path, channel_label)
    return [OSDStep(
        step_number=step_start,
        menu_path=menu_path,
        action=f"Adjust {channel_label}",
        value=value_label,
        rationale=rationale,
    )]


def _generate_cms_steps(tv_profile: Any, adj: Dict[str, Any], step_start: int = 1) -> List[OSDStep]:
    """Generate CMS/Colour Tuner OSD steps from an adjustment."""
    setting_key = adj.get("setting_key", "")
    parts = setting_key.split(".") if "." in setting_key else [setting_key]
    param_name = parts[-1] if len(parts) > 1 else setting_key

    colour_name = _extract_cms_colour(adj)

    cms_path = tv_profile.CMS_MENU_PATH if hasattr(tv_profile, "CMS_MENU_PATH") else ""
    if not cms_path:
        return []

    value_label = None
    if adj.get("to") is not None:
        value_label = _format_value(adj["to"], "slider")
    elif adj.get("delta") is not None:
        value_label = _format_delta(adj["delta"], "slider")

    rationale = adj.get("reason", "") or f"Adjust {param_name} for {colour_name}"
    menu_path = _build_cms_menu_path(cms_path, colour_name, param_name)

    return [OSDStep(
        step_number=step_start,
        menu_path=menu_path,
        action=f"Adjust {param_name}",
        value=value_label,
        rationale=rationale,
    )]


def _generate_gamma_steps(tv_profile: Any, adj: Dict[str, Any], step_start: int = 1) -> List[OSDStep]:
    """Generate gamma OSD steps from an adjustment."""
    gamma_path = tv_profile.GAMMA_MENU_PATH if hasattr(tv_profile, "GAMMA_MENU_PATH") else ""
    if not gamma_path:
        return []

    to_val = adj.get("to")
    delta = adj.get("delta")

    if to_val is not None:
        value_label = _format_value(to_val, "enum")
    elif delta is not None:
        value_label = _format_delta(delta, "slider")
    else:
        value_label = None

    rationale = adj.get("reason", "") or "Gamma adjustment to improve tracking."
    menu_path = _build_menu_path(gamma_path)

    return [OSDStep(
        step_number=step_start,
        menu_path=menu_path,
        action="Set gamma preset",
        value=value_label,
        rationale=rationale,
    )]


def _generate_picture_control_steps(tv_profile: Any, adj: Dict[str, Any], step_start: int = 1) -> List[OSDStep]:
    """Generate OSD steps for basic picture controls (backlight, contrast, etc)."""
    to_val = adj.get("to")
    delta = adj.get("delta")

    if to_val is not None:
        value_label = _format_value(to_val, "slider")
    elif delta is not None:
        value_label = _format_delta(delta, "slider")
    else:
        value_label = None

    # Extract setting name from setting_key when setting_name/setting not present
    raw_name = adj.get("setting_name", adj.get("setting"))
    if not raw_name:
        setting_key = adj.get("setting_key", "")
        raw_name = setting_key.split(".")[-1] if setting_key else "Setting"
    setting_name = raw_name.title()
    rationale = adj.get("reason", "") or f"Adjust {raw_name}"
    menu_path = _build_menu_path(f"Picture -> {setting_name}")

    return [OSDStep(
        step_number=step_start,
        menu_path=menu_path,
        action=f"Set {setting_name}",
        value=value_label,
        rationale=rationale,
    )]


def _generate_fallback_steps(
    tv_profile: Any, adj: Dict[str, Any], setting_key: str,
    setting_entry: Dict, step_start: int
) -> List[OSDStep]:
    """Generate steps for settings found in llm_schema but not matched by prefix."""
    setting_type = setting_entry.get("type", "slider")
    base_path = setting_entry.get("path", "")
    to_val = adj.get("to")
    delta = adj.get("delta")

    if to_val is not None:
        value_label = _format_value(to_val, setting_type)
    elif delta is not None:
        value_label = _format_delta(delta, setting_type)
    else:
        value_label = None

    rationale = adj.get("reason", "") or f"Adjust {setting_key}"

    if setting_type == "cms":
        colour_name = _extract_cms_colour(adj)
        params = setting_entry.get("params", {})
        param_name = list(params.keys())[0] if params else "Saturation"
        menu_path = _build_cms_menu_path(base_path, colour_name, param_name)
        return [OSDStep(
            step_number=step_start,
            menu_path=menu_path,
            action=f"Adjust {param_name}",
            value=value_label,
            rationale=rationale,
        )]

    if setting_type == "2pt_wb" or (setting_type == "slider" and "channels" in setting_entry):
        channels = setting_entry.get("channels", {})
        channel_part = setting_key.split(".")[-1] if "." in setting_key else setting_key
        channel_info = channels.get(channel_part, {})
        channel_label = channel_info.get("label", channel_part.replace("_", " ").title())
        wb_path = tv_profile.WB_MENU_PATH if hasattr(tv_profile, "WB_MENU_PATH") and tv_profile.WB_MENU_PATH else base_path
        if not wb_path:
            wb_path = base_path
        is_gain = "gain" in channel_part.lower() and "offset" not in channel_part.lower()
        ire_label = "IRE 80" if is_gain else "IRE 20"
        menu_path = _build_menu_path(wb_path, f"{ire_label} -> {channel_label}")
        return [OSDStep(
            step_number=step_start,
            menu_path=menu_path,
            action=f"Adjust {channel_label}",
            value=value_label,
            rationale=rationale,
        )]

    setting_name = setting_entry.get("term", setting_key.replace("_", " ").title())
    menu_path = _build_menu_path(base_path) if base_path else f"Picture -> {setting_key}"

    return [OSDStep(
        step_number=step_start,
        menu_path=menu_path,
        action=f"Set {setting_name}",
        value=value_label,
        rationale=rationale,
    )]


def _generate_steps_for_adjustment(tv_profile: Any, adj: Dict[str, Any], step_start: int = 1) -> List[OSDStep]:
    """Route an adjustment to the correct step generator based on setting type."""
    setting_key = adj.get("setting_key", "")

    if setting_key.startswith("white_balance_"):
        return _generate_wb_steps(tv_profile, adj, step_start)
    if setting_key.startswith("cms."):
        return _generate_cms_steps(tv_profile, adj, step_start)
    if setting_key in ("gamma", "white_balance_20pt"):
        return _generate_gamma_steps(tv_profile, adj, step_start)
    if setting_key in ("backlight", "black_level", "contrast", "color_temperature", "color_space"):
        return _generate_picture_control_steps(tv_profile, adj, step_start)

    # Fallback: try to look up in llm_schema.settings
    settings = tv_profile.llm_schema.get("settings", {}) if hasattr(tv_profile, "llm_schema") else {}
    setting_entry = settings.get(setting_key)
    if setting_entry:
        return _generate_fallback_steps(tv_profile, adj, setting_key, setting_entry, step_start)

    # Unknown setting - return a generic step
    setting_name = adj.get("setting_name", adj.get("setting", "Unknown Setting")).title()
    value_label = None
    if adj.get("to") is not None:
        value_label = _format_value(adj["to"], "slider")
    elif adj.get("delta") is not None:
        value_label = _format_delta(adj["delta"], "slider")
    return [OSDStep(
        step_number=step_start,
        menu_path=f"Picture -> {setting_name}",
        action=f"Adjust {setting_name}",
        value=value_label,
        rationale=adj.get("reason", "Manual adjustment required.") or "Manual adjustment required.",
    )]


def _check_adb_skip(tv_profile: Any) -> tuple:
    """Check if this TV supports ADB-based direct control (skip OSD).

    Returns (uses_adb, adb_instructions).
    Hisense U8G supports ADB CMS control - for CMS adjustments, we can skip OSD.
    """
    if tv_profile.short_name == "u8g":
        return True, (
            "Use the ADB CMS controls in this app to apply corrections directly. "
            "Open the Color Tuner step and click 'Apply' on each adjustment row."
        )
    return False, None


def _has_schema_for_setting(tv_profile: Any, adj: Dict[str, Any]) -> bool:
    """Check if the TV profile has schema data for a given adjustment."""
    setting_key = adj.get("setting_key", "")

    # Check direct match in settings
    settings = tv_profile.llm_schema.get("settings", {}) if hasattr(tv_profile, "llm_schema") else {}
    if setting_key in settings:
        return True

    # Check nested (e.g. white_balance_2pt.channels.R_gain)
    if "." in setting_key:
        parent_key = setting_key.split(".")[0]
        if parent_key in settings:
            return True

    # Check TV profile has relevant path attributes
    if setting_key.startswith("white_balance_") and tv_profile.WB_MENU_PATH:
        return True
    if setting_key.startswith("cms.") and tv_profile.CMS_MENU_PATH:
        return True
    if setting_key == "gamma" and tv_profile.GAMMA_MENU_PATH:
        return True

    known_settings = {"backlight", "black_level", "contrast", "color_temperature", "color_space"}
    return setting_key in known_settings


def translate_corrections(
    adjustments: List[Dict[str, Any]],
    tv_profile: Any,
) -> OSDTranslationResult:
    """Translate a list of LLM adjustment dicts into ordered OSD navigation steps.

    Parameters
    ----------
    adjustments : list of dict
        Adjustment dicts from the LLM (AdjustmentPlan.adjustments format).
    tv_profile : TVProfile
        The user's TV profile (from calibrator.profiles).

    Returns
    -------
    OSDTranslationResult with ordered steps, or empty steps + skipped_reasons.
    """
    result = OSDTranslationResult(
        tv_model=tv_profile.name,
        tv_key=tv_profile.short_name,
    )

    if not adjustments:
        return result

    # Normalise all adjustments
    normalised = [_normalise_adjustment(a) for a in adjustments]

    # Filter out corrections that can't be mapped
    valid_adjustments = []
    for adj in normalised:
        setting_key = adj.get("setting_key", "")
        if not setting_key:
            result.skipped_reasons.append(
                f"Cannot map adjustment '{adj.get('setting_name', adj.get('setting', '?'))}': unknown setting."
            )
            continue

        # Check if we have schema data for this setting
        has_schema = _has_schema_for_setting(tv_profile, adj)
        if not has_schema and setting_key.startswith(("white_balance_", "cms.", "gamma", "backlight", "black_level", "contrast")):
            # Known category but no schema — still generate steps from TV profile paths
            valid_adjustments.append(adj)
        elif has_schema:
            valid_adjustments.append(adj)
        else:
            result.skipped_reasons.append(
                f"Cannot map adjustment '{adj.get('setting_name', adj.get('setting', '?'))}': no menu path found for this setting on {tv_profile.name}."
            )
            continue

        has_schema = _has_schema_for_setting(tv_profile, adj)
        if not has_schema and setting_key.startswith(("white_balance_", "cms.", "gamma", "backlight", "black_level", "contrast")):
            # Known category but no schema - still generate steps from TV profile paths
            valid_adjustments.append(adj)
        elif has_schema:
            valid_adjustments.append(adj)
        else:
            result.skipped_reasons.append(
                f"Cannot map adjustment '{adj.get('setting', '?')}': no menu path found for this setting on {tv_profile.name}."
            )

    if not valid_adjustments:
        return result

    # Generate steps for each adjustment
    step_num = 1
    all_steps: List[OSDStep] = []

    for adj in valid_adjustments:
        steps = _generate_steps_for_adjustment(tv_profile, adj, step_num)
        all_steps.extend(steps)
        step_num += len(steps)

    # Check if ADB can handle all corrections
    uses_adb, adb_instructions = _check_adb_skip(tv_profile)
    if uses_adb:
        for step in all_steps:
            step.rationale += " (Can be applied via ADB - skip manual OSD navigation.)"

    result.steps = all_steps
    result.uses_adb = uses_adb
    result.adb_instructions = adb_instructions
    return result


def translate_from_adjustment_plan(
    plan_dict: Dict[str, Any],
    tv_profile: Any,
) -> OSDTranslationResult:
    """Convenience wrapper that accepts an AdjustmentPlan dict (from LLM).

    Parameters
    ----------
    plan_dict : dict
        Dict with keys: adjustments, next_step, confidence.
    tv_profile : TVProfile

    Returns
    -------
    OSDTranslationResult
    """
    adjustments = plan_dict.get("adjustments", [])
    return translate_corrections(adjustments, tv_profile)
