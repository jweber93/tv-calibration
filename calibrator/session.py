"""Session storage, workflow state, and response-shaping helpers."""

from __future__ import annotations

import json
import logging
import math
import threading
import uuid
from dataclasses import asdict, replace as dc_replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException

from calcore.models import (
    CalMode,
    CalibrationTarget,
    DV_TARGET,
    HDR10_TARGET,
    Measurement,
    SDR_TARGET,
)
from calcore.eotf import pq_target_nits
from .file_watcher import _measurement_signature
from .profiles import TV_PROFILES, TVProfile
from .guidance import (
    FINE_GAMMA_TRACKING_LEVELS,
    GAMMA_TRACKING_LEVELS,
    GAMMA_WORKFLOW_LABELS,
    GAMMA_WORKFLOW_LEVELS,
    gamma_nominal_pct,
    gamma_recommendations,
    gamma_target_for_measurement,
    luminance_control_plan,
    measurement_stimulus_pct,
    preset_gamma_control_plan,
    target_nits_for_colour,
    target_xy_for_colour,
    u8g_gamma_control_plan,
    wb_control_plan,
    wb_hints,
    wb_measurement_type,
    wb_recommendations,
    cms_control_plan,
    cms_hints,
)
from .zro_import import (
    SESSION_BREAK_SECONDS as ZRO_SESSION_BREAK_SECONDS,
    ZROImportResult,
    parse_zro_csv,
)
from .utils import (
    delta_e_ciede2000_xyY,
    delta_xy,
    eotf_from_luminance,
    stimulus_pct_from_code_value,
)

logger = logging.getLogger(__name__)

TARGETS: Dict[str, CalibrationTarget] = {
    "SDR": SDR_TARGET,
    "HDR10": HDR10_TARGET,
    "Dolby Vision": DV_TARGET,
}

MODE_OPTIONS = [
    {"key": "SDR", "label": "SDR", "desc": "Rec.709 · BT.1886", "peak_nits": 120},
    {
        "key": "HDR10",
        "label": "HDR10",
        "desc": "Rec.2020 · PQ (ST.2084) · 1000 nits",
        "peak_nits": 1000,
    },
    {
        "key": "Dolby Vision",
        "label": "Dolby Vision",
        "desc": "Rec.2020 · PQ (ST.2084) · 1000 nits",
        "peak_nits": 1000,
    },
]

SDR_AMBIENT_GUIDE = [
    {"label": "Dark (home theater)", "min": 80, "max": 100, "recommended": 100},
    {"label": "Dim (lights low)", "min": 100, "max": 140, "recommended": 120},
    {"label": "Bright (lamps on)", "min": 140, "max": 200, "recommended": 160},
    {"label": "Very bright / daylight", "min": 200, "max": 250, "recommended": 200},
]

# Grayscale ramp options keyed by step count.
GRAYSCALE_RAMP_OPTIONS: Dict[int, Dict[str, Any]] = {
    11: {
        "label": "11-step (10% intervals)",
        "summary": "Standard ramp — works with the free LightSpace Connect tier.",
        "interval": 10,
        "requires_paid": False,
    },
    21: {
        "label": "21-step (5% intervals)",
        "summary": "Better accuracy — still within the 25-patch free-tier limit.",
        "interval": 5,
        "requires_paid": False,
    },
    51: {
        "label": "51-step (2% intervals)",
        "summary": "Highest accuracy — requires the paid LightSpace Connect tier (no patch limit).",
        "interval": 2,
        "requires_paid": True,
    },
}

GRAYSCALE_RAMP_OPTIONS_VIEW = [
    {
        "steps": k,
        "label": v["label"],
        "summary": v["summary"],
        "requires_paid": v["requires_paid"],
    }
    for k, v in GRAYSCALE_RAMP_OPTIONS.items()
]

STEPS_ORDER = [
    "select_mode",
    "prepare",
    "pre_grayscale",
    "luminance",
    "white_balance",
    "gamma",
    "color_tuner",
    "post_grayscale",
    "suggested_patches",
    "report",
]

# Repatch is a transient meta-state that can occur during any measurement step
REPATCH_MAX_PASSES = 3

# Pass-decision actions that repass() understands (#659). Any other value is
# rejected rather than silently mutating the session step.
REPASS_ACTIONS = ("accept", "repatch", "ceiling")

# Cap on stored convergence-loop rounds per session to bound growth (#337)
_MAX_ADJUSTMENT_ROUNDS = 20

STEP_LABELS = {
    "select_mode": "Mode",
    "prepare": "Prepare TV",
    "pre_grayscale": "Pre-Cal Grayscale",
    "luminance": "Luminance",
    "white_balance": "White Balance",
    "gamma": "Gamma",
    "color_tuner": "Color Tuner",
    "post_grayscale": "Post-Cal Grayscale",
    "suggested_patches": "Patch Optimizer",
    "report": "Report",
}

CMS_PATCHES: Dict[str, Tuple[int, int, int]] = {
    "Red": (235, 16, 16),
    "Green": (16, 235, 16),
    "Blue": (16, 16, 235),
    "Cyan": (16, 235, 235),
    "Magenta": (235, 16, 235),
    "Yellow": (235, 235, 16),
}

PATTERN_GENERATOR_OPTIONS: Dict[str, Dict[str, Any]] = {
    "lightspace_connect": {
        "label": "LightSpace Connect",
        "desc": "Apple TV pattern generator driven from ColourSpace over the network",
        "signal_range_hint": "Usually Limited Range for TV workflows unless you have explicitly set Apple TV/HDMI to Full.",
    },
    "dogegen": {
        "label": "Dogegen",
        "desc": "External HDR-capable pattern generator workflow for ColourSpace",
        "signal_range_hint": "Often Full Range on PC-driven HDMI paths. For HDR10 Dogegen on PC, use 10-bit Full and match the app to Full as well.",
    },
}


def grayscale_levels_for_ramp(ramp_steps: int) -> List[int]:
    opt = GRAYSCALE_RAMP_OPTIONS.get(ramp_steps)
    interval = opt["interval"] if opt else 10
    return list(range(0, 101, interval))


def lim(pct: float) -> int:
    if pct <= 0:
        return 16
    if pct >= 100:
        return 235
    return round(16 + (pct / 100.0) * (235 - 16))


def full(pct: float) -> int:
    if pct <= 0:
        return 0
    if pct >= 100:
        return 255
    return round(pct / 100.0 * 255)


def full10(pct: float) -> int:
    if pct <= 0:
        return 0
    if pct >= 100:
        return 1023
    return round(pct / 100.0 * 1023)


def cv(pct: float, signal_range: str, code_scale: str = "8bit") -> int:
    if signal_range == "full" and code_scale == "10bit":
        return full10(pct)
    return full(pct) if signal_range == "full" else lim(pct)


def cms_patches(
    signal_range: str, code_scale: str = "8bit"
) -> Dict[str, Tuple[int, int, int]]:
    if signal_range == "full" and code_scale == "10bit":
        hi, lo = 1023, 0
        return {
            "Red": (hi, lo, lo),
            "Green": (lo, hi, lo),
            "Blue": (lo, lo, hi),
            "Cyan": (lo, hi, hi),
            "Magenta": (hi, lo, hi),
            "Yellow": (hi, hi, lo),
        }
    if signal_range != "full":
        return CMS_PATCHES
    hi, lo = 255, 0
    return {
        "Red": (hi, lo, lo),
        "Green": (lo, hi, lo),
        "Blue": (lo, lo, hi),
        "Cyan": (lo, hi, hi),
        "Magenta": (hi, lo, hi),
        "Yellow": (hi, hi, lo),
    }


def workflow_setup(mode: str, pattern_generator: str) -> List[Dict[str, str]]:
    app_name = "ColourSpace ZRO / Zero"
    if pattern_generator == "dogegen":
        if mode == "SDR":
            return [
                {
                    "step": f"Open {app_name}",
                    "detail": f"Launch {app_name} on your Windows PC.",
                },
                {
                    "step": "New Session",
                    "detail": "File → New Session. Set display type to your TV model.",
                },
                {
                    "step": "Set colour space target",
                    "detail": "In Display Settings, set: Colour Space = Rec.709, White Point = D65, EOTF = BT.1886 (or 2.2 for brighter rooms).",
                },
                {
                    "step": "Start Dogegen",
                    "detail": "Use the Start Dogegen button in the Dogegen Companion card. The app launches Dogegen with resolve_sdr so ColourSpace can connect automatically. Confirm Dogegen is on the HDMI path you will calibrate and the TV is not showing an HDR badge.",
                },
                {
                    "step": f"Connect {app_name} to Dogegen",
                    "detail": f"In {app_name} go to Hardware → Pattern Generator → Resolve. Dogegen is already listening; ColourSpace should connect automatically.",
                },
                {
                    "step": "Fullscreen Dogegen",
                    "detail": "Alt+Enter to fullscreen the Dogegen window on your calibration display. This bypasses the Windows compositor and ensures bit-accurate SDR output.",
                },
                {
                    "step": "Configure patch settings in ColourSpace",
                    "detail": "Set patch size to ~30%, background to black. Confirm the signal-range setting in this app matches Dogegen's GPU output range (Full Range / 0–255 for PC).",
                },
                {
                    "step": "Set CSV export folder",
                    "detail": "In ColourSpace preferences, set the auto-export folder to the same folder you configured as your Watch Folder in this app.",
                },
                {
                    "step": "Set TV picture mode",
                    "detail": "On your TV, select the recommended picture mode shown in the Prepare TV step, then reset all WB/CMS/Gamma controls to factory defaults as listed.",
                },
            ]
        if mode == "HDR10":
            return [
                {
                    "step": f"Open {app_name}",
                    "detail": f"Launch {app_name} on your Windows PC.",
                },
                {
                    "step": "New Session",
                    "detail": "File → New Session. Set display type to your TV model.",
                },
                {
                    "step": "Set colour space target",
                    "detail": "In Display Settings, set Target Gamut / EOTF = ST2084 Rec2020. Use D65 as the white point target.",
                },
                {
                    "step": "Start Dogegen",
                    "detail": "Use the Start Dogegen button in the Dogegen Companion card. The app launches Dogegen with mode 10_hdr, maxcll, and resolve_hdr — no manual mode selection needed. Confirm your TV shows an HDR10 or HDR badge before measuring.",
                },
                {
                    "step": f"Connect {app_name} to Dogegen",
                    "detail": f"In {app_name} go to Hardware → Pattern Generator → Resolve. Dogegen is already listening; ColourSpace should connect automatically.",
                },
                {
                    "step": "Fullscreen Dogegen",
                    "detail": "Alt+Enter to fullscreen the Dogegen window on your calibration display for bit-accurate 10-bit HDR output.",
                },
                {
                    "step": "Configure patch settings",
                    "detail": "The app launches Dogegen with a fixed window size (set via Window % in the Dogegen Companion card, default 10%). Make sure the signal-range selection in this app matches Dogegen's GPU output range — for HDR on PC this is usually Full Range. Use 3% only as a secondary peak-highlight cross-check.",
                },
                {
                    "step": "Use 10-bit manual values when needed",
                    "detail": "If you enter patches manually in ColourSpace, use full-range 10-bit RGB values (0 to 1023). The repo includes tools/reference/colorspace_zro_hdr10_21step_sequence.csv and tools/reference/dogegen_hdr10_full_range_10bit_reference.csv for this workflow.",
                },
                {
                    "step": "Set CSV export folder",
                    "detail": "Point ColourSpace auto-export to the same folder as your Watch Folder in this app.",
                },
                {
                    "step": "Set TV picture mode",
                    "detail": "Select the HDR picture mode shown in Prepare TV. Leave local dimming at your real viewing setting for HDR, usually High on the U8G. Reset WB/CMS controls.",
                },
            ]
        return [
            {
                "step": "Verify Dolby Vision signal path",
                "detail": "If your Dogegen workflow can hold an active Dolby Vision path, verify the TV is already in Dolby Vision before measuring. Dolby Vision must be active on the HDMI path you are calibrating.",
            },
            {
                "step": f"Open {app_name}",
                "detail": f"Launch {app_name} on your Windows PC.",
            },
            {
                "step": "New Session",
                "detail": "File → New Session. Set display type to your TV.",
            },
            {
                "step": "Set colour space target",
                "detail": "In Display Settings, set: Colour Space = Rec.2020, White Point = D65, EOTF = PQ (ST.2084). Note: DV applies internal tone mapping so absolute nit readings may not match HDR10.",
            },
            {
                "step": "Configure Dogegen",
                "detail": "Use the Dolby Vision-capable Dogegen path you have verified on this TV. If DV is not active and stable, use the HDR10 workflow instead.",
            },
            {
                "step": "Set CSV export folder",
                "detail": "Point ColourSpace auto-export to your Watch Folder.",
            },
            {
                "step": "Set TV picture mode",
                "detail": "Select the Dolby Vision picture mode (e.g. Dolby Vision Dark). DV handles tone-mapping internally — reset WB/CMS controls but gamma adjustment has limited effect.",
            },
        ]

    if mode == "SDR":
        return [
            {
                "step": f"Open {app_name}",
                "detail": f"Launch {app_name} on your Windows PC.",
            },
            {
                "step": "New Session",
                "detail": "File → New Session. Set display type to your TV model.",
            },
            {
                "step": "Set colour space target",
                "detail": "In Display Settings, set: Colour Space = Rec.709, White Point = D65, EOTF = BT.1886 (or 2.2).",
            },
            {
                "step": "Connect LightSpace Connect",
                "detail": "In ColourSpace go to Hardware → Network Server → LightSpace Connect. Enter the IP address shown in the LightSpace Connect app on your Apple TV. Click Connect.",
            },
            {
                "step": "Configure patch settings",
                "detail": "Set patch size to ~30%, background to black. For SDR leave HDR mode off.",
            },
            {
                "step": "Set CSV export folder",
                "detail": "In ColourSpace preferences, set the auto-export folder to the same folder you configured as your Watch Folder in this app.",
            },
            {
                "step": "Set TV picture mode",
                "detail": "On your TV, select the recommended picture mode shown in the Prepare TV step, then reset all WB/CMS/Gamma controls to factory defaults as listed.",
            },
        ]
    if mode == "HDR10":
        return [
            {
                "step": "Set Apple TV to HDR10 first",
                "detail": "On Apple TV: Settings → Display & Brightness → set Format to 4K HDR. Confirm your TV shows an HDR10 or HDR badge on the HDMI input.",
            },
            {
                "step": f"Open {app_name}",
                "detail": f"Launch {app_name} on your Windows PC.",
            },
            {
                "step": "New Session",
                "detail": "File → New Session. Set display type to your TV model.",
            },
            {
                "step": "Set colour space target",
                "detail": "In Display Settings, set: Colour Space = Rec.2020, White Point = D65, EOTF = PQ (ST.2084).",
            },
            {
                "step": "Connect LightSpace Connect",
                "detail": "Hardware → Network Server → LightSpace Connect. Enter Apple TV IP. Enable HDR mode in LightSpace Connect settings so patches are sent inside the HDR signal path.",
            },
            {
                "step": "Configure patch settings",
                "detail": "Set a 10% HDR window on black background. Ensure HDR is enabled in both ColourSpace and LightSpace Connect. Use 3% only as a secondary peak-highlight cross-check.",
            },
            {
                "step": "Set CSV export folder",
                "detail": "Point ColourSpace auto-export to the same folder as your Watch Folder in this app.",
            },
            {
                "step": "Set TV picture mode",
                "detail": "Select the HDR picture mode shown in Prepare TV. Leave local dimming at your real viewing setting for HDR, usually High on the U8G. Reset WB/CMS controls.",
            },
        ]
    return [
        {
            "step": "Verify Dolby Vision signal path",
            "detail": "Your Apple TV must already be outputting Dolby Vision: Settings → Display & Brightness → set Format to 4K Dolby Vision. Confirm TV shows Dolby Vision badge. ColourSpace measures within the DV tone-mapping pipeline — you cannot force DV from outside.",
        },
        {
            "step": f"Open {app_name}",
            "detail": f"Launch {app_name} on your Windows PC.",
        },
        {
            "step": "New Session",
            "detail": "File → New Session. Set display type to your TV.",
        },
        {
            "step": "Set colour space target",
            "detail": "In Display Settings, set: Colour Space = Rec.2020, White Point = D65, EOTF = PQ (ST.2084). Note: DV applies internal tone mapping so absolute nit readings may not match HDR10.",
        },
        {
            "step": "Connect LightSpace Connect",
            "detail": "Hardware → Network Server → LightSpace Connect. Enter Apple TV IP. LightSpace Connect must send patches through the active DV signal path.",
        },
        {
            "step": "Set CSV export folder",
            "detail": "Point ColourSpace auto-export to your Watch Folder.",
        },
        {
            "step": "Set TV picture mode",
            "detail": "Select the Dolby Vision picture mode (e.g. Dolby Vision Dark). DV handles tone-mapping internally — reset WB/CMS controls but gamma adjustment has limited effect.",
        },
    ]


def grayscale_ramp_instructions(
    step: str,
    ramp_steps: int,
    signal_range: str = "limited",
    code_scale: str = "8bit",
) -> Dict[str, Any]:
    opt = GRAYSCALE_RAMP_OPTIONS.get(ramp_steps, GRAYSCALE_RAMP_OPTIONS[11])
    interval = opt["interval"]
    levels = list(range(0, 101, interval))
    step_count = len(levels)
    preset_desc = f"{step_count}-step preset: 0, {interval}, {interval * 2} … 100%"
    patches = [
        {"label": f"{p}% Gray", "rgb": [cv(p, signal_range, code_scale)] * 3}
        for p in levels
    ]
    if step == "pre_grayscale":
        return {
            "title": "Run Pre-Cal Grayscale in ZRO",
            "summary": f"Measure a full {step_count}-point grayscale ramp (0–100% in {interval}% steps) before making any adjustments.",
            "steps": [
                {
                    "step": "Open grayscale workflow",
                    "detail": f"In ZRO, go to Measure → Grayscale Tracking (or Ramp). Select the {preset_desc}.",
                },
                {
                    "step": "Verify LightSpace Connect",
                    "detail": "Confirm LightSpace Connect is connected — ZRO will send each patch to your Apple TV automatically.",
                },
                {
                    "step": "Run the ramp",
                    "detail": "Click Run / Start. ZRO will display each gray patch, wait for stabilisation, and take a reading. Do not adjust any TV controls during this pass.",
                },
                {
                    "step": "Export CSV",
                    "detail": "After the ramp completes, ZRO auto-exports (or use File → Export → CSV). The Watch Folder will pick it up automatically, or use the Upload CSV button below.",
                },
            ],
            "patches": patches,
        }
    return {
        "title": "Run Post-Cal Grayscale in ZRO",
        "summary": f"Repeat the full {step_count}-point grayscale ramp to verify improvement after calibration.",
        "steps": [
            {
                "step": "Run the grayscale ramp again",
                "detail": f"Measure → Grayscale Tracking → {preset_desc}. Same as before — ZRO will step through each patch automatically.",
            },
            {
                "step": "Export CSV",
                "detail": "Export after the ramp. The Watch Folder will import it and this app will show the before/after comparison.",
            },
        ],
        "patches": patches,
    }


def zro_step_instructions(
    signal_range: str,
    pattern_generator: str = "lightspace_connect",
    code_scale: str = "8bit",
) -> Dict[str, Dict[str, Any]]:
    def patch_cv(pct: float) -> int:
        return cv(pct, signal_range, code_scale)

    cms = cms_patches(signal_range, code_scale)
    generator_label = PATTERN_GENERATOR_OPTIONS.get(
        pattern_generator, PATTERN_GENERATOR_OPTIONS["lightspace_connect"]
    )["label"]
    if pattern_generator == "dogegen":
        patch_summary = "Use Dogegen to hold each patch on screen while you adjust the TV. ColourSpace measures whatever Dogegen is displaying."
        open_patch_step = {
            "step": "Open Dogegen patch controls",
            "detail": "Use Dogegen's manual patch controls to hold the exact RGB patch listed below on screen.",
        }
        color_workflow_step = {
            "step": "Run all 6 colours",
            "detail": "Measure Red, Green, Blue, Cyan, Magenta, and Yellow using Dogegen. You can trigger them from ColourSpace if your Dogegen path is integrated there, or hold each patch manually and export the CSV after the set.",
        }
    else:
        patch_summary = "Use LightSpace Connect's Calibration Gallery to hold each patch on screen while you adjust the TV. ColourSpace measures whatever is displayed."
        open_patch_step = {
            "step": "Open the Calibration Gallery",
            "detail": "On your Apple TV, open LightSpace Connect. From the main menu select Gallery → Calibration Gallery. This lets you display any patch manually without going through ColourSpace's Single Patch command.",
        }
        color_workflow_step = {
            "step": "Open Color workflow",
            "detail": "In ColourSpace, go to Measure → Primaries & Secondaries (or Colour Checker). This will cycle through Red, Green, Blue, Cyan, Magenta, Yellow patches automatically via LightSpace Connect.",
        }
    return {
        "luminance": {
            "title": "Set Peak Luminance, Black Level & Contrast",
            "summary": patch_summary,
            "steps": [
                open_patch_step,
                {
                    "step": "Set Brightness (black level) — 5% gray",
                    "detail": f"Display the 5% gray patch (R=G=B={patch_cv(5)}). Adjust TV Brightness until the patch is just barely visible above black — not crushed, not elevated.",
                },
                {
                    "step": "Measure black level in ColourSpace",
                    "detail": "With the 5% patch still on screen, take a Single Measure reading. Target is ~0.05–0.10 nits. If you overshoot, back off Brightness by 1 click at a time.",
                },
                {
                    "step": "Set Contrast (white clipping) — 95% gray",
                    "detail": f"Display the 95% gray patch (R=G=B={patch_cv(95)}). Adjust TV Contrast (not Backlight) until the brightest visible detail is just not clipping. If the TV has a built-in clipping indicator, use it. Otherwise reduce Contrast until the patch is uniform and smooth.",
                },
                {
                    "step": "Set peak nits — 100% white",
                    "detail": f"Display 100% white (R=G=B={patch_cv(100)}). In ColourSpace take a reading. Compare to the target nits shown below. Adjust Backlight/OLED Light, then re-measure. Repeat until within ±5 nits. Re-check 95% gray for clipping after any Backlight change.",
                },
                {
                    "step": "Export CSV",
                    "detail": "Once all three patches are set, export from ColourSpace (File → Export → CSV) so this app can record your peak luminance baseline.",
                },
            ],
            "patches": [
                {"label": "5% Gray (black level)", "rgb": [patch_cv(5)] * 3},
                {"label": "95% Gray (white level)", "rgb": [patch_cv(95)] * 3},
                {"label": "100% White (peak nits)", "rgb": [patch_cv(100)] * 3},
            ],
        },
        "white_balance": {
            "title": "White Balance in ZRO",
            "summary": "Measure 80% gray (Gain) and 30% gray (Offset) to guide 2-point white balance adjustments.",
            "steps": [
                {
                    "step": "Measure 80% gray (Gain)",
                    "detail": f"In ColourSpace, Single Patch → R={patch_cv(80)}, G={patch_cv(80)}, B={patch_cv(80)}. This is your Gain measurement. Export the CSV.",
                },
                {
                    "step": "Check Gain guidance",
                    "detail": "Review the control plan below. Make the suggested WB Gain moves on your TV (R/G/B Gain controls).",
                },
                {
                    "step": "Measure 30% gray (Offset)",
                    "detail": f"Single Patch → R={patch_cv(30)}, G={patch_cv(30)}, B={patch_cv(30)}. This is your Offset measurement. Export the CSV.",
                },
                {
                    "step": "Check Offset guidance",
                    "detail": "Make the suggested WB Offset moves. Settle Gain before touching Offset.",
                },
                {
                    "step": "Iterate",
                    "detail": "Alternate between 80% and 30% measurements, making one-click changes, until both are within ΔE < 2.",
                },
            ],
            "patches": [
                {"label": "80% Gray (WB Gain)", "rgb": [patch_cv(80)] * 3},
                {"label": "30% Gray (WB Offset)", "rgb": [patch_cv(30)] * 3},
            ],
        },
        "gamma": {
            "title": "Verify Gamma in ZRO",
            "summary": "Run a 20/40/60/80% pass to check gamma tracking. Adjust the TV's gamma preset or per-point controls.",
            "steps": [
                {
                    "step": "Measure 20, 40, 60, 80% gray",
                    "detail": f"In ColourSpace, measure Single Patch at each level: 20% = R/G/B {patch_cv(20)}, 40% = {patch_cv(40)}, 60% = {patch_cv(60)}, 80% = {patch_cv(80)}. Export CSV after all four.",
                },
                {
                    "step": "Check effective gamma",
                    "detail": "Review the gamma measurements below. Each point shows the effective gamma calculated from luminance.",
                },
                {
                    "step": "Adjust TV gamma",
                    "detail": "Follow the control plan. For the U8G use the Gamma Calibration per-point controls. For preset-only TVs, switch the preset (2.2 → BT.1886 → 2.4, etc.).",
                },
                {
                    "step": "Re-run the pass",
                    "detail": "After any adjustment, re-measure the full 20/40/60/80% set before making another change.",
                },
            ],
            "patches": [
                {"label": "20% Gray (gamma)", "rgb": [patch_cv(20)] * 3},
                {"label": "40% Gray (gamma)", "rgb": [patch_cv(40)] * 3},
                {"label": "60% Gray (gamma)", "rgb": [patch_cv(60)] * 3},
                {"label": "80% Gray (gamma)", "rgb": [patch_cv(80)] * 3},
            ],
        },
        "color_tuner": {
            "title": "Color Tuner (CMS) in ZRO",
            "summary": f"Measure each primary and secondary colour with {generator_label} to guide Hue, Saturation, and Brightness adjustments.",
            "steps": [
                color_workflow_step,
                {
                    "step": "Run all 6 colours",
                    "detail": f"Let ColourSpace measure all six colours in sequence using {generator_label}. Export the CSV after the full set.",
                },
                {
                    "step": "Check guidance per colour",
                    "detail": "This app will show Hue/Saturation/Brightness guidance for each colour. Make one-click adjustments on your TV.",
                },
                {
                    "step": "Re-measure changed colours",
                    "detail": "After adjusting a colour, re-measure just that patch and export again. Repeat until ΔE < 2.",
                },
            ],
            "patches": [
                {"label": "Red", "rgb": list(cms["Red"])},
                {"label": "Green", "rgb": list(cms["Green"])},
                {"label": "Blue", "rgb": list(cms["Blue"])},
                {"label": "Cyan", "rgb": list(cms["Cyan"])},
                {"label": "Magenta", "rgb": list(cms["Magenta"])},
                {"label": "Yellow", "rgb": list(cms["Yellow"])},
            ],
        },
    }


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def serialize_measurement(m: Measurement) -> Dict[str, Any]:
    data = asdict(m)
    data["stimulus_rgb"] = list(data["stimulus_rgb"])
    return data


def deserialize_measurement(data: Dict[str, Any]) -> Measurement:
    payload = dict(data)
    payload["stimulus_rgb"] = tuple(payload.get("stimulus_rgb", [255, 255, 255])[:3])
    return Measurement(**payload)


def serialize_session(s: Dict[str, Any]) -> Dict[str, Any]:
    llm_cfg = s.get("llm_config", {})
    return {
        "id": s["id"],
        "tv_key": s["tv_key"],
        "tv_name": s["tv_name"],
        "step": s["step"],
        "mode": s["mode"],
        "gamma_workflow": s.get("gamma_workflow", "quick"),
        "signal_range": s.get("signal_range", "full"),
        "code_scale": s.get("code_scale", "8bit"),
        "lightspace_tier": s.get("lightspace_tier", "free"),
        "pattern_generator": s.get("pattern_generator", "dogegen"),
        "grayscale_ramp_steps": s.get("grayscale_ramp_steps", 11),
        "sdr_peak_nits": s.get("sdr_peak_nits"),
        "pre_measurements": [serialize_measurement(m) for m in s["pre_measurements"]],
        "post_measurements": [serialize_measurement(m) for m in s["post_measurements"]],
        "wb_measurements": [serialize_measurement(m) for m in s["wb_measurements"]],
        "lum_measurements": [serialize_measurement(m) for m in s["lum_measurements"]],
        "gamma_measurements": [
            serialize_measurement(m) for m in s["gamma_measurements"]
        ],
        "cms_measurements": [serialize_measurement(m) for m in s["cms_measurements"]],
        "peak_luminance": s["peak_luminance"],
        "created_at": s["created_at"],
        "last_accessed_at": s.get("last_accessed_at", s["created_at"]),
        "zro_imports": s.get("zro_imports", []),
        "llm_config": {
            "endpoint": llm_cfg.get("endpoint", ""),
            "model": llm_cfg.get("model", ""),
            # api_key intentionally omitted from serialization for security
            "temperature": llm_cfg.get("temperature", 0.2),
            "timeout": llm_cfg.get("timeout", 30.0),
        },
        "repass_count": s.get("repass_count", 0),
        "llm_adjustment_rounds": s.get("llm_adjustment_rounds", []),
        "tv_settings": s.get("tv_settings", {}),
        "_history_recorded": s.get("_history_recorded", False),
        "repass_decision": s.get("repass_decision", {}),
    }


_VALID_SESSION_MODES = {"SDR", "HDR10", "Dolby Vision"}


def deserialize_session(data: Dict[str, Any]) -> Dict[str, Any]:
    for required in ("id", "tv_key", "tv_name", "step"):
        if required not in data:
            raise ValueError(f"Session file missing required field: {required!r}")
    mode = data.get("mode")
    if mode is not None and mode not in _VALID_SESSION_MODES:
        raise ValueError(
            f"Session file has invalid mode: {mode!r}. "
            f"Valid values: {sorted(_VALID_SESSION_MODES)}"
        )
    current_time = now().isoformat()
    return {
        "id": data["id"],
        "tv_key": data["tv_key"],
        "tv_name": data["tv_name"],
        "step": data["step"],
        "mode": mode,
        "gamma_workflow": data.get("gamma_workflow", "quick"),
        "signal_range": data.get("signal_range", "full"),
        "code_scale": data.get("code_scale", "8bit"),
        "lightspace_tier": data.get("lightspace_tier", "free"),
        "pattern_generator": data.get("pattern_generator", "dogegen"),
        "grayscale_ramp_steps": data.get("grayscale_ramp_steps", 11),
        "target": dc_replace(SDR_TARGET, peak_luminance_nits=data["sdr_peak_nits"])
        if mode == "SDR" and data.get("sdr_peak_nits")
        else TARGETS.get(mode)
        if mode
        else None,
        "sdr_peak_nits": data.get("sdr_peak_nits"),
        "pre_measurements": [
            deserialize_measurement(m) for m in data.get("pre_measurements", [])
        ],
        "post_measurements": [
            deserialize_measurement(m) for m in data.get("post_measurements", [])
        ],
        "wb_measurements": [
            deserialize_measurement(m) for m in data.get("wb_measurements", [])
        ],
        "lum_measurements": [
            deserialize_measurement(m) for m in data.get("lum_measurements", [])
        ],
        "gamma_measurements": [
            deserialize_measurement(m) for m in data.get("gamma_measurements", [])
        ],
        "cms_measurements": [
            deserialize_measurement(m) for m in data.get("cms_measurements", [])
        ],
        "peak_luminance": data.get("peak_luminance", 0.0),
        "created_at": data.get("created_at", current_time),
        "last_accessed_at": data.get(
            "last_accessed_at", data.get("created_at", current_time)
        ),
        "zro_imports": data.get("zro_imports", []),
        "llm_config": {
            "endpoint": data.get("llm_config", {}).get("endpoint", ""),
            "model": data.get("llm_config", {}).get("model", ""),
            "api_key": "",  # api_key defaults to empty on deserialization
            "temperature": data.get("llm_config", {}).get("temperature", 0.2),
            "timeout": data.get("llm_config", {}).get("timeout", 30.0),
        },
        "repass_count": data.get("repass_count", 0),
        "llm_adjustment_rounds": data.get("llm_adjustment_rounds", []),
        "tv_settings": data.get("tv_settings", {}),
        "_history_recorded": data.get("_history_recorded", False),
        "repass_decision": data.get("repass_decision", {}),
    }


def measurement_time_bounds(
    bucket_map: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Optional[str]]:
    timestamps: List[str] = []
    for measurements in bucket_map.values():
        for measurement in measurements:
            ts = measurement.get("timestamp")
            if ts:
                timestamps.append(ts)
    if not timestamps:
        return {"measurement_start": None, "measurement_end": None}
    return {
        "measurement_start": min(timestamps),
        "measurement_end": max(timestamps),
    }


def grayscale_label_for_context(stim_pct: float) -> str:
    if stim_pct <= 0.5:
        return "Black (0%)"
    if stim_pct >= 99.5:
        return "White (100%)"
    return f"{stim_pct:.0f}% Gray"


def retag_measurement_dict(meas: Dict[str, Any], decode_range: str) -> Dict[str, Any]:
    m = deserialize_measurement(meas)
    if m.stimulus_rgb:
        stim_pct = stimulus_pct_from_code_value(m.stimulus_rgb[0], decode_range)
        m.label = grayscale_label_for_context(stim_pct)
    return serialize_measurement(m)


def recontextualize_import_result(
    result: ZROImportResult,
    signal_range: str,
    code_scale: str,
) -> ZROImportResult:
    if not (signal_range == "full" and code_scale == "10bit"):
        return result
    decode_range = "full10"

    def retag_list(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [retag_measurement_dict(item, decode_range) for item in items]

    result.pre_measurements = retag_list(result.pre_measurements)
    result.post_measurements = retag_list(result.post_measurements)
    result.grayscale_passes = [retag_list(items) for items in result.grayscale_passes]

    grayscale_items = [item for group in result.grayscale_passes for item in group]
    result.lum_measurements = []
    result.wb_measurements = []
    result.gamma_measurements = []
    for item in grayscale_items:
        m = deserialize_measurement(item)
        if not m.stimulus_rgb:
            continue
        stim_pct = stimulus_pct_from_code_value(m.stimulus_rgb[0], decode_range)
        if stim_pct >= 99.0:
            result.lum_measurements.append(
                serialize_measurement(dc_replace(m, label="White (100%)"))
            )
        if abs(stim_pct - 80.0) <= 7.0:
            result.wb_measurements.append(
                serialize_measurement(
                    dc_replace(m, label=f"WB Gain ({stim_pct:.0f}% gray)")
                )
            )
        elif abs(stim_pct - 30.0) <= 7.0:
            result.wb_measurements.append(
                serialize_measurement(
                    dc_replace(m, label=f"WB Offset ({stim_pct:.0f}% gray)")
                )
            )
        gamma_targets = [pct for pct in range(5, 100, 5) if abs(stim_pct - pct) <= 7.0]
        if gamma_targets:
            nearest = min(gamma_targets, key=lambda pct: abs(stim_pct - pct))
            result.gamma_measurements.append(
                serialize_measurement(dc_replace(m, label=f"Gamma {nearest:.0f}%"))
            )

    for row in result.raw_rows:
        if row.get("r") == row.get("g") == row.get("b"):
            row["stimulus_pct"] = stimulus_pct_from_code_value(
                int(row["r"]), decode_range
            )

    return result


def warnings_for_session_step(
    result: ZROImportResult, session_step: Optional[str]
) -> List[str]:
    if session_step in ("pre_grayscale", "post_grayscale"):
        return (
            result.grayscale_pass_warnings[-1] if result.grayscale_pass_warnings else []
        )
    return result.abl_warnings


def gamma_levels_for_session(session: Dict[str, Any]) -> Tuple[int, ...]:
    workflow = session.get("gamma_workflow", "quick")
    return GAMMA_WORKFLOW_LEVELS.get(workflow, GAMMA_TRACKING_LEVELS)


def gamma_workflows_for_tv(tv: TVProfile) -> List[Dict[str, str]]:
    workflows = getattr(tv, "GAMMA_WORKFLOWS", ["quick"])
    return [
        {"id": workflow, "label": GAMMA_WORKFLOW_LABELS.get(workflow, workflow.title())}
        for workflow in workflows
    ]


def gamma_workflow_instructions(
    workflow: str,
    signal_range: str = "limited",
    pattern_generator: str = "lightspace_connect",
    code_scale: str = "8bit",
) -> Dict[str, Any]:
    if workflow == "fine":
        return {
            "title": "Run Fine Gamma in ZRO",
            "summary": "Run a 5%-increment gamma pass to tune TVs that expose 5% input-level gamma controls.",
            "steps": [
                {
                    "step": "Measure 5% through 95% gray",
                    "detail": "In ZRO, run a grayscale/gamma pass at 5% increments: 5, 10, 15 ... 95%. Export the CSV after the pass completes.",
                },
                {
                    "step": "Use 5% gamma controls",
                    "detail": "Match each measurement to the nearest TV Gamma Calibration input level and make small moves only where the curve is off.",
                },
                {
                    "step": "Rerun the full fine pass",
                    "detail": "After any gamma-control changes, rerun the 5%-increment pass before making more adjustments.",
                },
            ],
            "patches": [
                {
                    "label": f"{p}% Gray (gamma)",
                    "rgb": [cv(p, signal_range, code_scale)] * 3,
                }
                for p in FINE_GAMMA_TRACKING_LEVELS
            ],
        }
    return zro_step_instructions(signal_range, pattern_generator, code_scale)["gamma"]


def m_to_dict(
    m: Measurement,
    target: CalibrationTarget,
    signal_range: str = "auto",
    code_scale: str = "8bit",
) -> Dict[str, Any]:
    stim_pct = 100.0
    if m.stimulus_rgb:
        decode_range = (
            "full10"
            if signal_range == "full" and code_scale == "10bit"
            else signal_range
        )
        stim_pct = (
            gamma_nominal_pct(m)
            if (m.label or "").startswith("Gamma ")
            else stimulus_pct_from_code_value(m.stimulus_rgb[0], decode_range)
        )

    cms_colour_names = {"Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"}
    colour_name = (m.label or "") if (m.label or "") in cms_colour_names else None
    is_grayscale_patch = (
        m.stimulus_rgb is not None
        and m.stimulus_rgb[0] == m.stimulus_rgb[1] == m.stimulus_rgb[2]
    )
    if colour_name and target:
        ref_xy = target_xy_for_colour(target, colour_name)
        ref_Y = target_nits_for_colour(target, colour_name)
    elif is_grayscale_patch and target.gamma > 0 and target.peak_luminance_nits > 0:
        ref_xy = target.white_point_xy
        ref_Y = target.peak_luminance_nits * (stim_pct / 100.0) ** target.gamma
    elif is_grayscale_patch and target.eotf.lower().startswith("pq") and target.peak_luminance_nits > 0:
        ref_xy = target.white_point_xy
        n = stim_pct / 100.0
        ref_Y = pq_target_nits(n, target.peak_luminance_nits)
    else:
        ref_xy = target.white_point_xy
        ref_Y = m.Y

    warnings: List[str] = []
    is_black_patch = stim_pct <= 0.5
    if not math.isfinite(m.Y) or not math.isfinite(m.x) or not math.isfinite(m.y):
        warnings.append("Measurement contains non-finite xyY values.")
    if (m.x <= 0 or m.y <= 0) and not (m.x == 0 and m.y == 0 and is_black_patch):
        warnings.append("Chromaticity values are missing or zero.")
    if m.Y < 0:
        warnings.append("Patch measured below 0 nits.")
    elif m.Y == 0 and not is_black_patch:
        warnings.append("Non-black patch measured at 0 nits.")
    invalid = len(warnings) > 0

    de = (
        None
        if invalid
        else delta_e_ciede2000_xyY(
            m.x,
            m.y,
            m.Y,
            ref_xy[0],
            ref_xy[1],
            ref_Y,
        )
    )
    dxy = None if invalid else delta_xy((m.x, m.y), ref_xy)
    eff_gamma: Optional[float] = None
    if (
        not invalid
        and 0 < stim_pct < 100
        and m.Y > 0
        and target.peak_luminance_nits > 0
    ):
        g = eotf_from_luminance(m.Y, target.peak_luminance_nits, stim_pct, target.eotf)
        eff_gamma = round(g, 3) if g is not None else None
    rating = (
        "invalid"
        if invalid
        else "excellent"
        if de <= 1.0
        else "good"
        if de <= 2.0
        else "acceptable"
        if de <= 3.0
        else "poor"
    )
    data = asdict(m)
    data["stimulus_rgb"] = list(data["stimulus_rgb"])
    return {
        **data,
        "stimulus_pct": stim_pct,
        "cct": round(m.cct, 0) if m.cct is not None else None,
        "delta_e": None if de is None else round(de, 2),
        "delta_xy": None if dxy is None else round(dxy, 4),
        "effective_gamma": eff_gamma,
        "rating": rating,
        "invalid": invalid,
        "warnings": warnings,
        "warning_summary": "; ".join(warnings) if warnings else "",
    }


def validate_peak_luminance(
    measured_nits: float, target: CalibrationTarget
) -> Dict[str, Any]:
    mode = str(target.mode)
    lower_bound = 1.0
    upper_bound = 1000.0 if target.mode == CalMode.SDR else 5000.0
    if not math.isfinite(measured_nits):
        return {
            "valid": False,
            "severity": "error",
            "code": "non_finite",
            "message": "Peak luminance reading is not a real number. Re-measure 100% white.",
            "bounds": {"min": lower_bound, "max": upper_bound},
            "mode": mode,
        }
    if measured_nits < lower_bound:
        return {
            "valid": False,
            "severity": "error",
            "code": "too_low",
            "message": f"100% white measured only {measured_nits:.1f} nits — too low to trust. Re-measure and check that all dimming features are disabled.",
            "bounds": {"min": lower_bound, "max": upper_bound},
            "mode": mode,
        }
    if measured_nits > upper_bound:
        return {
            "valid": False,
            "severity": "error",
            "code": "too_high",
            "message": f"100% white measured {measured_nits:.1f} nits — above the plausible range. Verify the meter is reading the panel, not ambient light.",
            "bounds": {"min": lower_bound, "max": upper_bound},
            "mode": mode,
        }
    return {
        "valid": True,
        "severity": "ok",
        "code": "ok",
        "message": "",
        "bounds": {"min": lower_bound, "max": upper_bound},
        "mode": mode,
    }


def bucket_map_for_session_step(
    result: ZROImportResult,
    session_step: Optional[str],
) -> Dict[str, List[Dict[str, Any]]]:
    bucket_map: Dict[str, List[Dict[str, Any]]] = {
        "cms_measurements": [],
        "pre_measurements": [],
        "post_measurements": [],
        "lum_measurements": [],
        "wb_measurements": [],
        "gamma_measurements": [],
    }
    latest_grayscale = result.grayscale_passes[-1] if result.grayscale_passes else []
    # Mirrors csv_adapter._PRE_STEPS / _POST_STEPS: select_mode/prepare and
    # suggested_patches/report are legitimate steps for a ZRO import to land
    # in (an early ramp export, or a post-report re-verification ramp), not
    # just pre_grayscale/post_grayscale — routing only those two left every
    # other step's import silently discarded (#636).
    if session_step in ("pre_grayscale", "prepare", "select_mode"):
        bucket_map["pre_measurements"] = latest_grayscale or result.pre_measurements
    elif session_step == "luminance":
        bucket_map["lum_measurements"] = result.lum_measurements
    elif session_step == "white_balance":
        bucket_map["wb_measurements"] = result.wb_measurements
    elif session_step == "gamma":
        bucket_map["gamma_measurements"] = result.gamma_measurements
    elif session_step == "color_tuner":
        bucket_map["cms_measurements"] = result.cms_measurements
    elif session_step in ("post_grayscale", "suggested_patches", "report"):
        bucket_map["post_measurements"] = (
            latest_grayscale or result.post_measurements or result.pre_measurements
        )
    return bucket_map


def _gray_bucket_for_step(session_step):
    """Return the grayscale session bucket that the current step writes to."""
    if session_step == "pre_grayscale":
        return "pre_measurements"
    if session_step == "post_grayscale":
        return "post_measurements"
    return None


def latest_gamma_pass(
    measurements: List[Measurement],
    levels: Tuple[int, ...] = GAMMA_TRACKING_LEVELS,
    signal_range: str = "auto",
    code_scale: str = "8bit",
    detect_sessions: bool = True,
) -> List[Measurement]:
    if detect_sessions:
        latest_reversed: List[Measurement] = []
        prev_dt: Optional[datetime] = None
        for measurement in reversed(measurements):
            ts = measurement.timestamp
            if not ts:
                if latest_reversed:
                    break
                continue
            try:
                current_dt = datetime.fromisoformat(ts)
            except ValueError:
                if latest_reversed:
                    break
                continue
            if prev_dt is not None:
                if current_dt > prev_dt:
                    break
                gap = (prev_dt - current_dt).total_seconds()
                if gap >= ZRO_SESSION_BREAK_SECONDS:
                    break
            latest_reversed.append(measurement)
            prev_dt = current_dt
        latest = list(reversed(latest_reversed))
    else:
        latest = list(measurements)
    latest_by_target: Dict[int, Measurement] = {}
    for measurement in latest:
        target_pct = gamma_target_for_measurement(
            measurement, levels, signal_range, code_scale
        )
        if target_pct is None:
            continue
        latest_by_target[target_pct] = dc_replace(
            measurement, label=f"Gamma {target_pct}%"
        )
    return [latest_by_target[pct] for pct in levels if pct in latest_by_target]


def latest_grayscale_pass(
    measurements: List[Measurement],
    max_count: int = 51,
    signal_range: str = "auto",
    code_scale: str = "8bit",
    detect_sessions: bool = True,
) -> List[Measurement]:
    """Extract the latest contiguous grayscale pass from a list of measurements.

    Parameters
    ----------
    measurements :
        Raw grayscale Measurement objects from the session.
    max_count :
        Maximum number of measurements to collect (default 51).
    signal_range, code_scale :
        Used to decode stimulus percentage from RGB code values.
    detect_sessions :
        When True (default), use timestamp gaps to detect session breaks.
        Set to False for freshly imported CSV data where the entire batch
        should be treated as a single coherent pass.
    """
    if not measurements:
        return []

    if detect_sessions:
        # Walk backwards through measurements, stopping at session gaps
        latest_reversed: List[Measurement] = []
        prev_dt: Optional[datetime] = None
        for measurement in reversed(measurements):
            ts = measurement.timestamp
            if not ts:
                if latest_reversed:
                    break
                continue
            try:
                current_dt = datetime.fromisoformat(ts)
            except ValueError:
                if latest_reversed:
                    break
                continue
            if prev_dt is not None:
                gap = (prev_dt - current_dt).total_seconds()
                if gap >= ZRO_SESSION_BREAK_SECONDS:
                    break
            latest_reversed.append(measurement)
            prev_dt = current_dt
            if len(latest_reversed) == max_count:
                break
        latest = list(reversed(latest_reversed))
    else:
        # Use all measurements — assume they form a single coherent pass
        # (appropriate for freshly imported CSV data)
        latest = list(measurements)

    decode_range = (
        "full10" if signal_range == "full" and code_scale == "10bit" else signal_range
    )
    return sorted(
        latest,
        key=lambda m: (
            stimulus_pct_from_code_value(m.stimulus_rgb[0], decode_range)
            if m.stimulus_rgb
            else float("inf")
        ),
    )


def gamma_pass_complete(
    measurements: List[Measurement],
    levels: Tuple[int, ...] = GAMMA_TRACKING_LEVELS,
    signal_range: str = "auto",
    code_scale: str = "8bit",
) -> bool:
    return len(
        latest_gamma_pass(measurements, levels, signal_range, code_scale)
    ) == len(levels)


def measurement_warning_messages(measurements: List[Dict[str, Any]]) -> List[str]:
    messages: List[str] = []
    for measurement in measurements:
        summary = (measurement.get("warning_summary") or "").strip()
        if summary and summary not in messages:
            messages.append(summary)
    return messages


def latest_wb_measurements(
    measurements: List[Measurement],
) -> Dict[str, Optional[Measurement]]:
    latest: Dict[str, Optional[Measurement]] = {"gain": None, "offset": None}
    for measurement in measurements:
        measurement_type = wb_measurement_type(measurement)
        if measurement_type in latest:
            latest[measurement_type] = measurement
    return latest


def recommended_code_scale(
    pattern_generator: str,
    signal_range: str,
    mode: Optional[str],
) -> str:
    if (
        pattern_generator == "dogegen"
        and signal_range == "full"
        and mode in ("HDR10", "Dolby Vision")
    ):
        return "10bit"
    return "8bit"


def session_view(s: Dict[str, Any]) -> Dict[str, Any]:
    from .quality import step_quality

    tv = TV_PROFILES[s["tv_key"]]
    target: Optional[CalibrationTarget] = s.get("target")
    step = s["step"]
    mode = s.get("mode")
    signal_range = s.get("signal_range", "limited")
    code_scale = s.get("code_scale", "8bit")

    view: Dict[str, Any] = {
        "id": s["id"],
        "tv_key": s["tv_key"],
        "step": step,
        "step_index": STEPS_ORDER.index(step) if step in STEPS_ORDER else 0,
        "steps": [{"id": key, "label": STEP_LABELS[key]} for key in STEPS_ORDER],
        "tv": s["tv_name"],
        "mode": mode,
        "peak_luminance": s["peak_luminance"],
        "created_at": s["created_at"],
        "date": s["created_at"],
        "zro_imports": s.get("zro_imports", []),
        "signal_range": signal_range,
        "code_scale": code_scale,
        "lightspace_tier": s.get("lightspace_tier", "free"),
        "pattern_generator": s.get("pattern_generator", "lightspace_connect"),
        "grayscale_ramp_steps": s.get("grayscale_ramp_steps", 11),
        "quality_gates": step_quality(s, tv),
        "repass_count": s.get("repass_count", 0),
        "adjustment_round_count": len(s.get("llm_adjustment_rounds", [])),
        "llm_config": {
            "endpoint": s.get("llm_config", {}).get("endpoint", ""),
            "model": s.get("llm_config", {}).get("model", ""),
            # api_key intentionally omitted — write-only
        },
    }

    if target:
        view["target"] = {
            "gamut": target.gamut,
            "eotf": target.eotf,
            "peak_nits": target.peak_luminance_nits,
            "white_point_xy": list(target.white_point_xy),
            "gamma": target.gamma,
        }

    if step == "prepare" and mode:
        mode_enum = CalMode(mode)
        pattern_generator = s.get("pattern_generator", "lightspace_connect")
        generator_meta = PATTERN_GENERATOR_OPTIONS.get(
            pattern_generator, PATTERN_GENERATOR_OPTIONS["lightspace_connect"]
        )
        view["prepare_data"] = {
            "picture_mode": tv.PICTURE_MODES.get(mode_enum, ""),
            "disable_settings": [
                {"setting": k, "value": v} for k, v in tv.DISABLE_BEFORE_CAL
            ],
            "configure_settings": [
                {"setting": k, "value": v} for k, v in tv.CONFIGURE_FOR_CAL
            ],
            "reset_settings": [
                {"setting": k, "value": v} for k, v in tv.RESET_BEFORE_CAL
            ],
            "picture_control_guide": [
                {"control": k, "instruction": v} for k, v in tv.PICTURE_CONTROL_GUIDE
            ],
            "wb_menu_path": tv.WB_MENU_PATH,
            "gamma_menu_path": tv.GAMMA_MENU_PATH,
            "gamma_note": tv.gamma_note,
            "is_hdr": mode in ("HDR10", "Dolby Vision"),
            "service_menu_access": tv.SERVICE_MENU_ACCESS,
            "service_menu_controls": [
                {"control": k, "desc": v} for k, v in tv.SERVICE_MENU_CONTROLS
            ]
            if tv.SERVICE_MENU_CONTROLS
            else [],
            "pattern_generator": pattern_generator,
            "pattern_generator_label": generator_meta["label"],
            "pattern_generator_desc": generator_meta["desc"],
            "pattern_generator_signal_range_hint": generator_meta["signal_range_hint"],
            "code_scale": code_scale,
            "code_scale_options": [
                {
                    "key": "8bit",
                    "label": "8-bit Codes",
                    "desc": "Use 0..255 for Full Range or 16..235 for Limited Range.",
                },
                {
                    "key": "10bit",
                    "label": "10-bit Codes",
                    "desc": "Use 0..1023 for Full Range workflows such as Dogegen HDR on PC.",
                },
            ],
            "pattern_generator_options": [
                {"key": key, "label": meta["label"], "desc": meta["desc"]}
                for key, meta in PATTERN_GENERATOR_OPTIONS.items()
            ],
            "zro_setup_steps": workflow_setup(mode, pattern_generator),
            "grayscale_ramp_options": GRAYSCALE_RAMP_OPTIONS_VIEW,
        }
    elif step in ("pre_grayscale", "post_grayscale"):
        ramp_steps = s.get("grayscale_ramp_steps", 11)
        levels = grayscale_levels_for_ramp(ramp_steps)
        raw_meas = (
            s["pre_measurements"] if step == "pre_grayscale" else s["post_measurements"]
        )
        meas = latest_grayscale_pass(
            raw_meas,
            max_count=len(levels),
            signal_range=signal_range,
            code_scale=code_scale,
        )
        measurement_dicts = (
            [m_to_dict(m, target, signal_range, code_scale) for m in meas]
            if target
            else []
        )
        view["grayscale_data"] = {
            "phase": "Pre-Cal" if step == "pre_grayscale" else "Post-Cal",
            "done": len(meas),
            "total": len(levels),
            "complete": len(meas) >= len(levels),
            "measurements": measurement_dicts,
            "warnings": measurement_warning_messages(measurement_dicts),
        }
        view["zro_instructions"] = grayscale_ramp_instructions(
            step, ramp_steps, signal_range, code_scale
        )
    elif step == "luminance":
        latest_lum = s["lum_measurements"][-1] if s["lum_measurements"] else None
        lum_validation = (
            validate_peak_luminance(latest_lum.Y, target)
            if latest_lum and target
            else None
        )
        view["luminance_data"] = {
            "target_nits": target.peak_luminance_nits if target else 120,
            "current_nits": s["peak_luminance"],
            "picture_control_guide": [
                {"control": k, "instruction": v} for k, v in tv.PICTURE_CONTROL_GUIDE
            ],
            "control_plan": luminance_control_plan(
                s["peak_luminance"],
                target.peak_luminance_nits if target else 120,
                lum_validation,
            ),
            "validation": lum_validation,
            "readings": [
                {"Y": round(m.Y, 1), "ts": m.timestamp}
                for m in s["lum_measurements"][-10:]
            ],
        }
        view["zro_instructions"] = zro_step_instructions(
            signal_range, s.get("pattern_generator", "lightspace_connect"), code_scale
        )["luminance"]
    elif step == "white_balance":
        wb = s["wb_measurements"]
        latest_wb = latest_wb_measurements(wb)
        latest_gain = latest_wb["gain"]
        latest_offset = latest_wb["offset"]
        current_hints = (
            wb_hints(wb[-1], target.white_point_xy) if wb and target else None
        )
        last_wb_de = (
            m_to_dict(wb[-1], target, signal_range, code_scale)["delta_e"]
            if wb and target
            else None
        )
        view["wb_data"] = {
            "menu_path": tv.WB_MENU_PATH,
            "controls": [{"control": k, "desc": v} for k, v in tv.WB_2POINT.items()],
            "notes": list(tv.WB_NOTES),
            "iterations": len(wb),
            "measurements": [
                m_to_dict(m, target, signal_range, code_scale) for m in wb[-4:]
            ]
            if target
            else [],
            "gain_measurement": m_to_dict(latest_gain, target, signal_range, code_scale)
            if latest_gain and target
            else None,
            "offset_measurement": m_to_dict(
                latest_offset, target, signal_range, code_scale
            )
            if latest_offset and target
            else None,
            "has_gain_measurement": latest_gain is not None,
            "has_offset_measurement": latest_offset is not None,
            "ready_to_continue": latest_gain is not None and latest_offset is not None,
            "hints": current_hints,
            "recommendations": wb_recommendations(
                wb[-1], current_hints, tv, de=last_wb_de
            )
            if wb and target and current_hints
            else [],
            "control_plan": wb_control_plan(wb[-1], current_hints, tv, de=last_wb_de)
            if wb and target and current_hints
            else [],
            "target_xy": list(target.white_point_xy) if target else [0.3127, 0.3290],
        }
        view["zro_instructions"] = zro_step_instructions(
            signal_range, s.get("pattern_generator", "lightspace_connect"), code_scale
        )["white_balance"]
    elif step == "gamma":
        g_meas = s["gamma_measurements"]
        gamma_levels = gamma_levels_for_session(s)
        latest_pass = latest_gamma_pass(g_meas, gamma_levels, signal_range, code_scale)
        gamma_workflow = s.get("gamma_workflow", "quick")
        view["gamma_data"] = {
            "menu_path": tv.GAMMA_MENU_PATH,
            "gamma_note": tv.gamma_note,
            "notes": list(tv.GAMMA_NOTES),
            "mode": mode,
            "workflow": gamma_workflow,
            "workflow_label": GAMMA_WORKFLOW_LABELS.get(
                gamma_workflow, gamma_workflow.title()
            ),
            "available_workflows": gamma_workflows_for_tv(tv),
            "tracking_levels": list(gamma_levels),
            "target_gamma": target.gamma if target else 2.2,
            "ready_to_continue": gamma_pass_complete(
                g_meas, gamma_levels, signal_range, code_scale
            ),
            "measurements": [
                m_to_dict(m, target, signal_range, code_scale) for m in latest_pass
            ]
            if target
            else [],
            "recommendations": gamma_recommendations(
                latest_pass,
                target.gamma if target else 2.2,
                target.peak_luminance_nits
                if target
                else SDR_TARGET.peak_luminance_nits,
                s["tv_key"],
                gamma_workflow,
                signal_range,
                code_scale,
                gamma_levels,
                target.eotf if target else "gamma",
            ),
            "control_plan": (
                u8g_gamma_control_plan(
                    latest_pass,
                    target.gamma if target else 2.2,
                    target.peak_luminance_nits
                    if target
                    else SDR_TARGET.peak_luminance_nits,
                    signal_range,
                    code_scale,
                    gamma_levels,
                    target.eotf if target else "gamma",
                )
                if s["tv_key"] == "u8g" and target
                else preset_gamma_control_plan(
                    latest_pass,
                    target.gamma if target else 2.2,
                    target.peak_luminance_nits
                    if target
                    else SDR_TARGET.peak_luminance_nits,
                    signal_range,
                    code_scale,
                    gamma_levels,
                    target.eotf if target else "gamma",
                )
                if target
                else []
            ),
        }
        view["zro_instructions"] = gamma_workflow_instructions(
            gamma_workflow,
            signal_range,
            s.get("pattern_generator", "lightspace_connect"),
            code_scale,
        )
    elif step == "color_tuner":
        cms = s["cms_measurements"]
        latest_colour = cms[-1].label.replace(" 100%", "") if cms else None
        latest_hint = (
            cms_hints(cms[-1], target, latest_colour)
            if cms and target and latest_colour
            else None
        )
        patch_map = cms_patches(signal_range, code_scale)
        view["cms_data"] = {
            "menu_path": tv.CMS_MENU_PATH,
            "controls": list(tv.CMS_CONTROLS),
            "colors": [
                {"name": name, "rgb": list(patch_map.get(name, (235, 235, 235)))}
                for name in tv.CMS_COLOURS
            ],
            "notes": list(tv.CMS_NOTES),
            "measurements": [
                m_to_dict(m, target, signal_range, code_scale) for m in cms
            ]
            if target
            else [],
            "latest_hint": latest_hint,
            "control_plan": cms_control_plan(
                latest_hint, list(tv.CMS_CONTROLS), latest_colour, tv
            )
            if latest_hint
            else [],
        }
        view["zro_instructions"] = zro_step_instructions(
            signal_range, s.get("pattern_generator", "lightspace_connect"), code_scale
        )["color_tuner"]
    return view



def _repass_target_step(current_step: str) -> str:
    """Determine which measurement step to jump back to for a repass.

    When the LLM decides to repatch, we jump to the step whose measurements
    correspond to the current phase.  For pre/post grayscale we go back to
    the grayscale step; for WB/gamma/color we go back to the respective step.
    """
    measurement_steps = {
        "pre_grayscale", "luminance", "white_balance",
        "gamma", "color_tuner", "post_grayscale",
    }
    if current_step in measurement_steps:
        return current_step
    if current_step == "suggested_patches":
        return "post_grayscale"
    if current_step == "report":
        return "post_grayscale"
    return "post_grayscale"


class SessionStore:
    def __init__(
        self,
        *,
        session_dir_getter: Callable[[], Path],
        ttl_getter: Callable[[], timedelta],
        watched_session_id_getter: Callable[[], Optional[str]],
    ) -> None:
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._session_dir_getter = session_dir_getter
        self._ttl_getter = ttl_getter
        self._watched_session_id_getter = watched_session_id_getter

    @property
    def session_dir(self) -> Path:
        return self._session_dir_getter()

    @property
    def session_ttl(self) -> timedelta:
        return self._ttl_getter()

    def touch_session(
        self, session: Dict[str, Any], *, when: Optional[datetime] = None
    ) -> None:
        session["last_accessed_at"] = (when or now()).isoformat()

    def delete_session_persisted_file(self, sid: str) -> None:
        path = self.session_dir / f"{sid}.json"
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.exception("Failed to delete session file for %s", sid)

    def is_session_expired(
        self, session: Dict[str, Any], *, current_time: Optional[datetime] = None
    ) -> bool:
        watched_id = self._watched_session_id_getter()
        if session.get("id") == watched_id:
            return False
        last_accessed_raw = session.get("last_accessed_at")
        if last_accessed_raw is None:
            # Backward-compatibility for ad-hoc in-memory sessions used by tests and
            # older callers: if no access timestamp exists yet, treat the session as live.
            return False
        anchor = parse_iso_dt(last_accessed_raw) or parse_iso_dt(
            session.get("created_at")
        )
        if anchor is None:
            return False
        return anchor <= (current_time or now()) - self.session_ttl

    def evict_expired_sessions(
        self, *, current_time: Optional[datetime] = None
    ) -> List[str]:
        active_time = current_time or now()
        with self._lock:
            expired_ids = [
                sid
                for sid, session in list(self.sessions.items())
                if self.is_session_expired(session, current_time=active_time)
            ]
            for sid in expired_ids:
                self.sessions.pop(sid, None)
                self.delete_session_persisted_file(sid)
            return expired_ids

    def save_session(self, sid: str) -> None:
        with self._lock:
            if sid not in self.sessions:
                return
            try:
                self.session_dir.mkdir(exist_ok=True)
                self.touch_session(self.sessions[sid])
                path = self.session_dir / f"{sid}.json"
                path.write_text(json.dumps(serialize_session(self.sessions[sid]), indent=2))
            except Exception:
                logger.exception("Failed to save session %s to disk", sid)

    def load_sessions(self) -> None:
        if not self.session_dir.exists():
            return
        with self._lock:
            for path in self.session_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text())
                    sid = data.get("id", path.stem)
                    if sid not in self.sessions and data.get("tv_key") in TV_PROFILES:
                        session = deserialize_session(data)
                        if self.is_session_expired(session):
                            self.delete_session_persisted_file(sid)
                            continue
                        self.sessions[sid] = session
                except Exception:
                    logger.warning(
                        "Could not load session from %s — file may be corrupt", path
                    )

    def get(self, sid: str) -> Dict[str, Any]:
        with self._lock:
            self.evict_expired_sessions()
            if sid not in self.sessions:
                raise HTTPException(404, "Session not found")
            session = self.sessions[sid]
            self.touch_session(session)
            return session

    def latest_session(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            self.evict_expired_sessions()
            if not self.sessions:
                return None
            return max(
                self.sessions.values(),
                key=lambda s: s.get("last_accessed_at", s.get("created_at", "")),
            )

    def create_session(
        self, tv_key: str, sdr_peak_nits: Optional[float] = None
    ) -> Dict[str, Any]:
        with self._lock:
            self.evict_expired_sessions()
            if tv_key not in TV_PROFILES:
                raise HTTPException(400, f"Unknown TV profile: {tv_key}")
            sid = str(uuid.uuid4())[:8]
            tv = TV_PROFILES[tv_key]
            created_at = now().isoformat()
            self.sessions[sid] = {
            "id": sid,
            "tv_key": tv_key,
            "tv_name": tv.name,
            "step": "select_mode",
            "mode": None,
            "gamma_workflow": "quick",
            "signal_range": "full",
            "code_scale": "8bit",
            "lightspace_tier": "free",
            "pattern_generator": "dogegen",
            "grayscale_ramp_steps": 11,
            "target": None,
            "sdr_peak_nits": sdr_peak_nits,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 0.0,
            "created_at": created_at,
            "last_accessed_at": created_at,
            "zro_imports": [],
            "llm_config": {
                "endpoint": "",
                "model": "",
                "api_key": "",
                "temperature": 0.2,
                "timeout": 30.0,
            },
            "repass_count": 0,
            "llm_adjustment_rounds": [],
        }
            return self.sessions[sid]

    def delete(self, sid: str) -> None:
        with self._lock:
            self.get(sid)
            self.sessions.pop(sid, None)
            self.delete_session_persisted_file(sid)

    def select_mode(
        self, sid: str, mode: str, sdr_peak_nits: Optional[float] = None
    ) -> Dict[str, Any]:
        with self._lock:
            session = self.get(sid)
            if session["step"] != "select_mode":
                raise HTTPException(400, "Not in select_mode step")
            if mode not in TARGETS:
                raise HTTPException(400, f"Unknown mode: {mode}")
            session["mode"] = mode
            if mode == "SDR" and sdr_peak_nits is not None:
                if not (50 <= sdr_peak_nits <= 400):
                    raise HTTPException(400, "sdr_peak_nits must be between 50 and 400")
                session["target"] = dc_replace(
                    SDR_TARGET, peak_luminance_nits=sdr_peak_nits
                )
                session["sdr_peak_nits"] = sdr_peak_nits
            else:
                session["target"] = TARGETS[mode]
                session.setdefault("sdr_peak_nits", None)
            session["code_scale"] = recommended_code_scale(
                session.get("pattern_generator", "lightspace_connect"),
                session.get("signal_range", "limited"),
                mode,
            )
            session["step"] = "prepare"
            self.save_session(sid)
        return session

    def confirm_prepared(self, sid: str) -> Dict[str, Any]:
        with self._lock:
            session = self.get(sid)
            if session["step"] != "prepare":
                raise HTTPException(400, "Not in prepare step")
            session["step"] = "pre_grayscale"
            self.save_session(sid)
        return session

    def set_gamma_workflow(self, sid: str, workflow: str) -> Dict[str, Any]:
        """Set the gamma calibration workflow for this session.

        Atomic: held under ``SessionStore._lock`` to prevent TOCTOU races
        with concurrent delete/eviction (#503).
        """
        with self._lock:
            session = self.get(sid)
            tv = TV_PROFILES[session["tv_key"]]
            available = set(getattr(tv, "GAMMA_WORKFLOWS", ["quick"]))
            if workflow not in available:
                raise HTTPException(400, f"Unsupported gamma workflow: {workflow}")
            session["gamma_workflow"] = workflow
            self.save_session(sid)
            return session

    def set_signal_range(self, sid: str, signal_range: str) -> Dict[str, Any]:
        """Set the HDMI signal range (limited or full).

        Atomic: held under ``SessionStore._lock`` to prevent TOCTOU races
        with concurrent delete/eviction (#503).
        """
        with self._lock:
            session = self.get(sid)
            if signal_range not in ("limited", "full"):
                raise HTTPException(400, "signal_range must be 'limited' or 'full'")
            session["signal_range"] = signal_range
            session["code_scale"] = recommended_code_scale(
                session.get("pattern_generator", "lightspace_connect"),
                signal_range,
                session.get("mode"),
            )
            self.save_session(sid)
            return session

    def set_pattern_generator(self, sid: str, pattern_generator: str) -> Dict[str, Any]:
        """Set the pattern generator source for this session.

        Atomic: held under ``SessionStore._lock`` to prevent TOCTOU races
        with concurrent delete/eviction (#503).
        """
        with self._lock:
            session = self.get(sid)
            if pattern_generator not in PATTERN_GENERATOR_OPTIONS:
                raise HTTPException(
                    400,
                    f"pattern_generator must be one of {sorted(PATTERN_GENERATOR_OPTIONS)}",
                )
            session["pattern_generator"] = pattern_generator
            session["code_scale"] = recommended_code_scale(
                pattern_generator,
                session.get("signal_range", "limited"),
                session.get("mode"),
            )
            self.save_session(sid)
            return session

    def set_code_scale(self, sid: str, code_scale: str) -> Dict[str, Any]:
        """Set the ADC code scale (8bit or 10bit).

        Atomic: held under ``SessionStore._lock`` to prevent TOCTOU races
        with concurrent delete/eviction (#503).
        """
        with self._lock:
            session = self.get(sid)
            if code_scale not in ("8bit", "10bit"):
                raise HTTPException(400, "code_scale must be '8bit' or '10bit'")
            if code_scale == "10bit" and session.get("signal_range") != "full":
                raise HTTPException(
                    400, "10bit code_scale currently requires Full signal range"
                )
            session["code_scale"] = code_scale
            self.save_session(sid)
            return session

    def set_lightspace_tier(
        self, sid: str, tier: str, ramp_steps: int
    ) -> Dict[str, Any]:
        """Set the LightSpace Connect tier and grayscale ramp step count.

        Atomic: held under ``SessionStore._lock`` to prevent TOCTOU races
        with concurrent delete/eviction (#503).
        """
        with self._lock:
            session = self.get(sid)
            if tier not in ("free", "paid"):
                raise HTTPException(400, "tier must be 'free' or 'paid'")
            if ramp_steps not in GRAYSCALE_RAMP_OPTIONS:
                raise HTTPException(
                    400, f"ramp_steps must be one of {sorted(GRAYSCALE_RAMP_OPTIONS)}"
                )
            if GRAYSCALE_RAMP_OPTIONS[ramp_steps]["requires_paid"] and tier != "paid":
                raise HTTPException(
                    400, f"{ramp_steps}-step ramp requires the paid LightSpace Connect tier"
                )
            session["lightspace_tier"] = tier
            session["grayscale_ramp_steps"] = ramp_steps
            self.save_session(sid)
            return session

    def set_grayscale_ramp(self, sid: str, ramp_steps: int) -> Dict[str, Any]:
        """Set the grayscale ramp step count for this session.

        Atomic: held under ``SessionStore._lock`` to prevent TOCTOU races
        with concurrent delete/eviction (#503).
        """
        with self._lock:
            session = self.get(sid)
            if ramp_steps not in GRAYSCALE_RAMP_OPTIONS:
                raise HTTPException(
                    400, f"ramp_steps must be one of {sorted(GRAYSCALE_RAMP_OPTIONS)}"
                )
            session["grayscale_ramp_steps"] = ramp_steps
            self.save_session(sid)
            return session

    def next_step(self, sid: str, luminance_threshold_pct: float) -> Dict[str, Any]:
        with self._lock:
            session = self.get(sid)
            step = session["step"]
            grayscale_levels = grayscale_levels_for_ramp(
                session.get("grayscale_ramp_steps", 11)
            )
            if step == "pre_grayscale":
                if len(session["pre_measurements"]) < len(grayscale_levels):
                    raise HTTPException(
                        400,
                        f"Need {len(grayscale_levels)} measurements, have {len(session['pre_measurements'])}",
                    )
                session["step"] = "luminance"
            elif step == "luminance":
                if not session["lum_measurements"]:
                    raise HTTPException(400, "Take at least one luminance reading first")
                if session.get("target") is None:
                    raise HTTPException(400, "Target not set. Go back and select a calibration mode.")
                latest = session["lum_measurements"][-1]
                target = session["target"]
                validation = validate_peak_luminance(latest.Y, target)
                if not validation["valid"]:
                    raise HTTPException(400, validation["message"])
                # SDR targets a fixed peak (e.g. 100/120 nits) achievable via Backlight,
                # so require the reading to land within tolerance of that target. HDR
                # targets (HDR10, Dolby Vision) carry a nominal mastering reference
                # (e.g. 1000 nits) that is not an achievable or desirable panel setting
                # — panel peak is display-limited and tone mapping handles the rest —
                # so HDR only needs to pass the sanity-bounds check above (#546).
                if target.mode == CalMode.SDR:
                    target_nits = target.peak_luminance_nits
                    pct_err = abs(latest.Y - target_nits) / target_nits * 100
                    if pct_err > luminance_threshold_pct:
                        raise HTTPException(
                            400,
                            f"Peak luminance is {latest.Y:.1f} nits but target is {target_nits:.0f} nits ({pct_err:.1f}% off). Adjust Backlight until the reading is within {luminance_threshold_pct:.0f}% of target before continuing.",
                        )
                session["step"] = "white_balance"
            elif step == "white_balance":
                latest_wb = latest_wb_measurements(session["wb_measurements"])
                if not latest_wb["gain"] or not latest_wb["offset"]:
                    raise HTTPException(
                        400, "Take both 80% gray and 30% gray white balance readings first"
                    )
                session["step"] = "gamma"
            elif step == "gamma":
                gamma_levels = gamma_levels_for_session(session)
                if not gamma_pass_complete(
                    session["gamma_measurements"],
                    gamma_levels,
                    session.get("signal_range", "auto"),
                    session.get("code_scale", "8bit"),
                ):
                    workflow_label = GAMMA_WORKFLOW_LABELS.get(
                        session.get("gamma_workflow", "quick"), "selected"
                    )
                    raise HTTPException(
                        400,
                        f"Run a full {workflow_label.lower()} gamma pass before continuing",
                    )
                session["step"] = "color_tuner"
            elif step == "color_tuner":
                session["post_measurements"] = []
                session["step"] = "post_grayscale"
            elif step == "post_grayscale":
                if len(session["post_measurements"]) < len(grayscale_levels):
                    raise HTTPException(
                        400,
                        f"Need {len(grayscale_levels)} measurements, have {len(session['post_measurements'])}",
                    )
                session["step"] = "report"
            else:
                raise HTTPException(400, f"No transition from step '{step}'")
            self.save_session(sid)
        return session

    def jump_to_step(self, sid: str, target_index: int) -> Dict[str, Any]:
        with self._lock:
            session = self.get(sid)
            current_index = (
                STEPS_ORDER.index(session["step"]) if session["step"] in STEPS_ORDER else 0
            )
            if target_index >= current_index:
                raise HTTPException(400, "Can only jump to a completed step")
            if target_index < 0 or target_index >= len(STEPS_ORDER):
                raise HTTPException(400, "Invalid step index")
            session["step"] = STEPS_ORDER[target_index]
            self.save_session(sid)
        return session

    def prev_step(self, sid: str) -> Dict[str, Any]:
        with self._lock:
            session = self.get(sid)
            transitions = {
                "prepare": "select_mode",
                "pre_grayscale": "prepare",
                "luminance": "pre_grayscale",
                "white_balance": "luminance",
                "gamma": "white_balance",
                "color_tuner": "gamma",
                "post_grayscale": "color_tuner",
                "report": "post_grayscale",
            }
            step = session["step"]
            if step not in transitions:
                raise HTTPException(400, f"No back transition from step '{step}'")
            if step == "prepare":
                session["mode"] = None
                session["target"] = None
            session["step"] = transitions[step]
            self.save_session(sid)
        return session


    def repass(
        self,
        sid: str,
        action: str,
        patches: Optional[List[str]] = None,
        reason: str = "",
        ceiling_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle an LLM pass-decision repass action.

        "ceiling" records the decision and stops. "repatch" increments
        repass_count, checks against REPATCH_MAX_PASSES, and jumps the
        session back to the relevant measurement step. "accept" records
        the decision and leaves the session step untouched (#659).

        Atomic: held under ``SessionStore._lock`` to prevent TOCTOU races
        with concurrent delete/eviction (#503).
        """
        with self._lock:
            session = self.get(sid)
            current_step = session["step"]

            if action == "ceiling":
                session.setdefault("repass_decision", {})
                session["repass_decision"]["action"] = "ceiling"
                session["repass_decision"]["reason"] = reason
                session["repass_decision"]["ceiling_reason"] = ceiling_reason
                session["repass_decision"]["timestamp"] = now().isoformat()
                self.save_session(sid)
                return session

            if action == "accept":
                session.setdefault("repass_decision", {})
                session["repass_decision"]["action"] = "accept"
                session["repass_decision"]["reason"] = reason
                session["repass_decision"]["patches"] = patches or []
                session["repass_decision"]["timestamp"] = now().isoformat()
                self.save_session(sid)
                return session

            if action == "repatch":
                session["repass_count"] = session.get("repass_count", 0) + 1
                if session["repass_count"] > REPATCH_MAX_PASSES:
                    session.setdefault("repass_decision", {})
                    session["repass_decision"]["action"] = "ceiling"
                    session["repass_decision"]["reason"] = (
                        f"Max repass limit ({REPATCH_MAX_PASSES}) reached"
                    )
                    session["repass_decision"]["ceiling_reason"] = ceiling_reason or "Exceeded maximum repass count"
                    session["repass_decision"]["timestamp"] = now().isoformat()
                    session["step"] = "report"
                    self.save_session(sid)
                    return session

                session.setdefault("repass_decision", {})
                session["repass_decision"]["action"] = "repatch"
                session["repass_decision"]["reason"] = reason
                session["repass_decision"]["patches"] = patches or []
                session["repass_decision"]["timestamp"] = now().isoformat()

                session["step"] = _repass_target_step(current_step)
                self.save_session(sid)
                return session

            raise ValueError(
                f"unknown repass action {action!r}; expected one of: "
                f"{', '.join(REPASS_ACTIONS)}"
            )

    def record_adjustment_round(
        self,
        sid: str,
        residual: Dict[str, Any],
        suggested: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Append a convergence-loop round (residual → suggested deltas) (#337).

        Held under the store RLock so the get → mutate → save sequence is atomic
        against concurrent callers (#256). The recorded ``residual`` is the state
        the ``suggested`` deltas were responding to; the next round's residual
        reveals whether those deltas converged.
        """
        with self._lock:
            session = self.get(sid)
            rounds = session.setdefault("llm_adjustment_rounds", [])
            rounds.append(
                {
                    "round": len(rounds) + 1,
                    "residual": residual,
                    "suggested": suggested,
                    "timestamp": now().isoformat(),
                }
            )
            # Bound session growth — the most recent rounds drive stall detection,
            # so keep the tail and drop the oldest.
            if len(rounds) > _MAX_ADJUSTMENT_ROUNDS:
                del rounds[:-_MAX_ADJUSTMENT_ROUNDS]
            self.save_session(sid)
            return session

    def _locked_import(
        self,
        sid: str,
        filename: Optional[str],
        bucket_map: Dict[str, List[dict]],
        step_warnings: List[str],
        total_rows: int,
        sessions_detected: int,
        colour_sessions: int,
        grayscale_sessions: int,
        session_breaks: int,
        unknown_rows: int,
        raw_rows: List,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Merge *bucket_map* into the session atomically under the store RLock.

        The RLock is reentrant (``threading.RLock``) so that
        ``save_session``'s own ``with self._lock:`` re-enters without
        deadlock.  This serialises the full get → mutate → save sequence
        against concurrent file-watcher auto-imports and other callers
        that share the same lock (``store._lock``).

        Append-mode buckets (wb, gamma, cms, lum) are deduplicated by
        measurement signature (timestamp, label, stimulus_rgb, Y, x, y)
        to prevent duplicate rows from cumulative ZRO exports.
        """
        with self._lock:
            session = self.get(sid)
            if session.get("mode") is None:
                raise HTTPException(
                    400, "Select a calibration mode before importing measurements."
                )
            counts: Dict[str, int] = {}
            duplicates_skipped = 0
            target_gray_bucket = _gray_bucket_for_step(session.get("step"))
            # Buckets that accumulate measurements across imports (not cleared per step)
            append_mode_buckets = {"wb_measurements", "gamma_measurements", "cms_measurements", "lum_measurements"}
            for key, meas_dicts in bucket_map.items():
                if key == target_gray_bucket and meas_dicts:
                    session[key] = []
                if key in append_mode_buckets:
                    existing_signatures = {
                        _measurement_signature(m) for m in session.setdefault(key, [])
                    }
                    appended = 0
                    for item in meas_dicts:
                        measurement = deserialize_measurement(item)
                        signature = _measurement_signature(measurement)
                        if signature in existing_signatures:
                            duplicates_skipped += 1
                            continue
                        session[key].append(measurement)
                        existing_signatures.add(signature)
                        appended += 1
                    if appended:
                        counts[key] = appended
                else:
                    for item in meas_dicts:
                        session[key].append(deserialize_measurement(item))
                    if meas_dicts:
                        counts[key] = len(meas_dicts)
            # Only re-derive peak_luminance from a measurement *this* import
            # actually placed in lum_measurements — matching
            # file_watcher._do_import_file's existing counts.get(...) gate —
            # never from whatever happened to land there last, or an import
            # that never touched lum_measurements silently overwrites a
            # correct peak reading (#655).
            if counts.get("lum_measurements") and session.get("lum_measurements"):
                last_lum = session["lum_measurements"][-1]
                y_val = getattr(last_lum, "Y", last_lum.get("Y") if isinstance(last_lum, dict) else None)
                if y_val is not None:
                    session["peak_luminance"] = round(y_val, 2)
            imported_at = now().isoformat()
            import_meta: Dict[str, Any] = {
                "filename": filename or "unknown",
                "timestamp": imported_at,
                "total_rows": total_rows,
                "sessions_detected": sessions_detected,
                "colour_sessions": colour_sessions,
                "grayscale_sessions": grayscale_sessions,
                "session_breaks": session_breaks,
                "abl_warnings": step_warnings,
                "unknown_rows": unknown_rows,
                "duplicates_skipped": duplicates_skipped,
                "buckets_populated": counts,
                "raw_rows": raw_rows,
                **measurement_time_bounds(bucket_map),
            }
            session.setdefault("zro_imports", []).append(import_meta)
            self.save_session(sid)
        return session, import_meta

    def import_zro_bytes(
        self,
        sid: str,
        filename: Optional[str],
        contents: bytes,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            result = parse_zro_csv(contents)
        except Exception as exc:
            raise HTTPException(400, f"ZRO CSV parse error: {exc}") from exc

        with self._lock:
            session = self.get(sid)
            result = recontextualize_import_result(
                result,
                session.get("signal_range", "full"),
                session.get("code_scale", "8bit"),
            )

        if result.total_rows == 0:
            raise HTTPException(
                400,
                "No valid measurement rows found. Expected a tab-separated ZRO export with columns: Date and time, R, G, B, Y, x, y, msec.",
            )

        with self._lock:
            session = self.get(sid)
            bucket_map = bucket_map_for_session_step(result, session.get("step"))
            step_warnings = warnings_for_session_step(result, session.get("step"))

        return self._locked_import(
            sid=sid,
            filename=filename,
            bucket_map=bucket_map,
            step_warnings=step_warnings,
            total_rows=result.total_rows,
            sessions_detected=result.sessions_detected,
            colour_sessions=result.colour_sessions,
            grayscale_sessions=result.grayscale_sessions,
            session_breaks=result.session_breaks,
            unknown_rows=result.unknown_rows,
            raw_rows=result.raw_rows,
        )

    def import_generic_bytes(
        self,
        sid: str,
        filename: Optional[str],
        contents: bytes,
        format_: str = "xyY",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Parse and import a generic (non-ZRO) CSV file.

        Parameters
        ----------
        sid : str
            Session ID.
        filename : Optional[str]
            Original filename (for import tracking).
        contents : bytes
            Raw CSV content.
        format_ : str, optional
            Headerless CSV column format — ``"xyY"`` (default) or ``"XYZ"``.
            Passed as ``format=`` to :func:`parse_measurement_csv`.
        """
        from .csv_adapter import patches_to_session_buckets
        from calcore.csv_import import parse_measurement_csv

        try:
            patches = parse_measurement_csv(contents, format=format_)
        except Exception as exc:
            raise HTTPException(400, f"Generic CSV parse error: {exc}") from exc

        with self._lock:
            session = self.get(sid)
            bucket_map = patches_to_session_buckets(
                patches,
                session["target"],
                session.get("step"),
                session.get("signal_range", "auto"),
                session.get("code_scale", "8bit"),
            )

        return self._locked_import(
            sid=sid,
            filename=filename,
            bucket_map=bucket_map,
            step_warnings=[],
            total_rows=len(patches),
            sessions_detected=1,
            colour_sessions=sum(1 for p in patches if not p.is_grayscale),
            grayscale_sessions=sum(1 for p in patches if p.is_grayscale),
            session_breaks=0,
            unknown_rows=0,
            raw_rows=[],
        )
