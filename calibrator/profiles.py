"""
TV profile dataclass and all built-in TV profiles.

Each profile encodes the menu paths, control names, and calibration guidance
for a specific TV model.  Add new TVs by calling _build_<model>_profile() and
registering it in TV_PROFILES.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from calcore.models import CalMode


@dataclass
class TVProfile:
    """
    Base TV profile defining the interface every supported TV must provide.
    Fill in all fields for a new TV model.
    """

    # Identity
    name: str = ""  # Human-readable, e.g. "Hisense U8G"
    short_name: str = ""  # For filenames, e.g. "u8g"

    # Recommended picture modes per calibration mode
    PICTURE_MODES: Dict[CalMode, str] = field(default_factory=dict)

    # Settings to disable before calibration
    DISABLE_BEFORE_CAL: List[Tuple[str, str]] = field(default_factory=list)

    # Settings to configure for calibration
    CONFIGURE_FOR_CAL: List[Tuple[str, str]] = field(default_factory=list)
    PICTURE_CONTROL_GUIDE: List[Tuple[str, str]] = field(default_factory=list)

    # White balance menu path and controls
    WB_MENU_PATH: str = ""
    WB_2POINT: Dict[str, str] = field(default_factory=dict)
    WB_NOTES: List[str] = field(default_factory=list)

    # Gamma / EOTF
    GAMMA_MENU_PATH: str = ""
    gamma_note: str = ""  # Optional per-TV note shown during gamma step
    GAMMA_NOTES: List[str] = field(default_factory=list)
    GAMMA_WORKFLOWS: List[str] = field(default_factory=lambda: ["quick"])

    # Optional UI automation hints
    AUTO_PRECAL_INTERVAL_MS: int = 0

    # Colour Management System (CMS)
    CMS_MENU_PATH: str = ""
    CMS_CONTROLS: List[str] = field(default_factory=list)
    CMS_COLOURS: List[str] = field(default_factory=list)
    CMS_NOTES: List[str] = field(default_factory=list)
    supported_gamuts: List[str] = field(default_factory=lambda: ["bt709"])

    # Settings to reset to factory defaults before calibrating.
    # These are separate from DISABLE_BEFORE_CAL because the goal here is not
    # just to turn things off but to return controls to a known neutral value so
    # prior calibration data doesn't skew your new measurements.
    # Each entry is (control_name, reset_value_or_instruction).
    RESET_BEFORE_CAL: List[Tuple[str, str]] = field(default_factory=list)

    # Optional hidden / service menu instructions
    SERVICE_MENU_ACCESS: str = ""
    SERVICE_MENU_CONTROLS: List[Tuple[str, str]] = field(default_factory=list)

    # Machine-readable settings schema for LLM injection (#99).
    # Maps setting key → dict with type, path, options/range, and dependency info.
    llm_schema: Dict[str, Any] = field(default_factory=dict)


def _build_u8g_profile() -> TVProfile:
    """Hisense U8G profile — the original supported TV."""
    return TVProfile(
        name="Hisense U8G",
        short_name="u8g",
        PICTURE_MODES={
            CalMode.SDR: "Theater Day (or Theater Night for dark rooms)",
            CalMode.HDR10: "HDR Theater",
            CalMode.DOLBY_VISION: "Dolby Vision Dark (or Dolby Vision Custom)",
        },
        DISABLE_BEFORE_CAL=[
            ("Adaptive Brightness", "Off"),
            ("Motion Smoothing / Motion Enhancement", "Off"),
            ("Noise Reduction", "Off"),
            ("Digital Noise Reduction", "Off"),
            ("HDMI Dynamic Range", "Auto (or Full if PC source)"),
            ("Active Contrast", "Off"),
            ("Super Resolution", "Off"),
            ("Colour Enhancement", "Off"),
        ],
        CONFIGURE_FOR_CAL=[
            (
                "Local Dimming",
                "High for HDR calibration/validation; use Off only as a troubleshooting cross-check if measurements look unstable",
            ),
            ("Backlight", "Adjust to target luminance"),
            ("Gamma", "BT.1886 for SDR (or 2.2)"),
            ("Colour Temperature", "Warm (closest to D65)"),
            ("Colour Space", "Auto or Native for measurement, Rec.709 for SDR target"),
            ("HDMI Format", "Enhanced (for 4K/HDR sources)"),
        ],
        PICTURE_CONTROL_GUIDE=[
            (
                "Brightness (Black Level)",
                "Show a 5% gray patch. Raise Brightness until 5% is just visible, then lower it one click if blacks look lifted.",
            ),
            (
                "Contrast (White Level)",
                "Show a 95% gray patch. Raise Contrast until near-white detail starts to clip, then back it down 1-2 clicks.",
            ),
            (
                "Backlight",
                "Show a 100% white patch and use Backlight to hit the luminance target for the current mode.",
            ),
        ],
        WB_MENU_PATH="Settings → Picture → Calibration Settings → White Balance → 2 Point",
        WB_2POINT={
            "R-Gain": "Adjusts red in highlights (80-100% gray)",
            "G-Gain": "Adjusts green in highlights (80-100% gray)",
            "B-Gain": "Adjusts blue in highlights (80-100% gray)",
            "R-Offset": "Adjusts red in shadows (20-30% gray)",
            "G-Offset": "Adjusts green in shadows (20-30% gray)",
            "B-Offset": "Adjusts blue in shadows (20-30% gray)",
        },
        WB_NOTES=[
            "Start in the 2 Point submenu. Leave 20 Point untouched until 2-point is already close.",
            "Use 80% gray for Gain moves and 30% gray for Offset moves.",
            "If you wander into Gamma Calibration while doing white balance, back out and return to White Balance.",
            "Keep RGB Only off while measuring.",
        ],
        GAMMA_MENU_PATH="Settings → Picture → Calibration Settings → Gamma / Gamma Calibration",
        gamma_note="The U8G may label this differently per firmware version.",
        GAMMA_WORKFLOWS=["quick", "fine"],
        GAMMA_NOTES=[
            "Set the main Gamma preset first. Use BT.1886 when the whole curve is too bright and 2.2 when the whole curve is too dark.",
            "Inside Gamma Calibration, Input Level steps run in 5-point increments (5, 10, 15 ... 100). Pick the Input Level closest to the gray patch you just measured.",
            "For the guided 20/40/60/80% pass, start with Input Level 20 for 20% gray, 40 for 40% gray, 60 for 60% gray, and 80 for 80% gray.",
            "If measured gamma is below target, that part of the image is too bright, so lower that Input Level control by 5. If measured gamma is above target, that part is too dark, so raise that Input Level control by 5.",
            "After every 5-point move, re-measure the same gray patch first. When that point is close, rerun the full 20/40/60/80% pass before making more changes.",
        ],
        AUTO_PRECAL_INTERVAL_MS=500,
        CMS_MENU_PATH="Settings → Picture → Calibration Settings → Colour Tuner",
        CMS_CONTROLS=["Hue", "Saturation", "Brightness"],
        CMS_COLOURS=["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"],
        CMS_NOTES=[
            "Open Colour Tuner, select the matching colour, then make small Hue/Saturation/Brightness moves.",
            "Measure fully saturated 100% colour patches while tuning each colour entry.",
            "Brightness in Hisense Colour Tuner acts like per-colour luminance.",
            "Keep changes small and re-measure before moving to the next colour.",
        ],
        RESET_BEFORE_CAL=[
            (
                "White Balance — Red/Green/Blue Gain",
                "0  (neutral; eliminates any prior calibration offsets in highlights)",
            ),
            (
                "White Balance — Red/Green/Blue Offset",
                "0  (neutral; eliminates any prior calibration offsets in shadows)",
            ),
            ("Colour Tuner — Hue (all colours)", "0  (no colour rotation)"),
            ("Colour Tuner — Saturation (all colours)", "0  (no saturation push/pull)"),
            (
                "Colour Tuner — Brightness (all colours)",
                "0  (no luminance offset per colour)",
            ),
            (
                "Brightness (black level)",
                "50 (factory default; wrong value clips blacks or lifts the floor)",
            ),
            (
                "Contrast (white level)",
                "90 (factory default for Theater mode; prevents highlight clipping)",
            ),
            (
                "Sharpness",
                "0  (no edge enhancement — sharpening can corrupt near-white patch readings)",
            ),
        ],
        supported_gamuts=["bt709", "p3d65", "bt2020"],
        llm_schema={
            "model": "Hisense U8G",
            "variants": ["55U8G", "65U8G", "75U8G"],
            "nomenclature": {
                "backlight": {
                    "term": "Backlight Level",
                    "function": "Overall panel illumination (nits). Sets peak white output. Does NOT affect black crush.",
                    "path": "Settings > Picture > Backlight > Backlight Level",
                },
                "black_level": {
                    "term": "Brightness",
                    "function": "Black level control. Sets the point where shadow detail clips. Calibration: set so near-black test patterns are just visible.",
                    "path": "Settings > Picture > Brightness",
                },
            },
            "settings": {
                "gamma": {
                    "path": "Settings > Picture > Calibration settings > Gamma",
                    "type": "enum",
                    "options": [2.0, 2.2, 2.4],
                    "increment": 0.2,
                    "default": 2.2,
                    "calibration_target": 2.4,
                    "note": "Only these three values available as presets.",
                },
                "local_dimming": {
                    "path": "Settings > Picture > Backlight > Dynamic Backlight Control",
                    "type": "enum",
                    "options": ["Off", "Low", "Medium", "High"],
                    "zones": 360,
                    "calibration_note": "Set High for HDR measurement; Off only as troubleshooting cross-check.",
                },
                "color_space": {
                    "path": "Settings > Picture > Advanced Settings > Color Space",
                    "type": "enum",
                    "options": ["Auto", "BT.709", "Rec.2020"],
                },
                "color_temperature": {
                    "path": "Settings > Picture > Advanced Settings > Color Temperature",
                    "type": "enum",
                    "options": ["Warm", "Cool"],
                    "calibration_target": "Warm",
                },
                "white_balance_2pt": {
                    "path": "Settings > Picture > Calibration settings > White Balance > 2 Point",
                    "type": "slider",
                    "channels": {
                        "R_gain": {
                            "label": "R-Gain",
                            "affects": "red highlights (80-100% gray)",
                        },
                        "G_gain": {
                            "label": "G-Gain",
                            "affects": "green highlights (80-100% gray)",
                        },
                        "B_gain": {
                            "label": "B-Gain",
                            "affects": "blue highlights (80-100% gray)",
                        },
                        "R_offset": {
                            "label": "R-Offset",
                            "affects": "red shadows (20-30% gray)",
                        },
                        "G_offset": {
                            "label": "G-Offset",
                            "affects": "green shadows (20-30% gray)",
                        },
                        "B_offset": {
                            "label": "B-Offset",
                            "affects": "blue shadows (20-30% gray)",
                        },
                    },
                    "neutral_value": 0,
                    "note": "Start with 2-point. Leave 20-point untouched until 2-point is already close.",
                },
                "white_balance_20pt": {
                    "path": "Settings > Picture > Calibration settings > White Balance > 20 Point",
                    "type": "multipoint",
                    "step_pct_options": [5, 10],
                    "neutral_value": 0,
                    "note": "Used for fine grayscale tracking after 2-point is close.",
                },
                "cms": {
                    "path": "Settings > Picture > Calibration settings > Color Tuner",
                    "type": "cms",
                    "colors": ["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"],
                    "params": {
                        "Hue": "Shifts relative colour angle.",
                        "Saturation": "Adjusts purity / vibrancy.",
                        "Brightness": "Controls luminance of that specific colour.",
                    },
                    "neutral_value": 0,
                    "note": "Zero all CMS values before loading a 3D LUT (ColourSpace, Calman).",
                },
                "contrast": {
                    "path": "Settings > Picture > Contrast",
                    "type": "slider",
                    "calibration_default": 90,
                    "note": "Controls peak white levels. Back down if 100% whites clip.",
                },
                "brightness": {
                    "path": "Settings > Picture > Brightness",
                    "type": "slider",
                    "calibration_default": 50,
                    "note": "Black level control — NOT luminance. Set so 5% near-black is just visible.",
                },
                "active_contrast": {
                    "path": "Settings > Picture > Advanced Settings > Active Contrast",
                    "type": "toggle",
                    "calibration_value": "Off",
                    "note": "Auto local contrast — disable during calibration.",
                },
                "hdmi_dynamic_range": {
                    "path": "Settings > Picture > Advanced Settings > HDMI Dynamic Range",
                    "type": "enum",
                    "options": ["Auto", "Full"],
                    "dependency": "Only available when HDMI input is active.",
                    "calibration_note": "Set Auto (or Full if PC source).",
                },
            },
        },
    )


def _build_tcl7105x_profile() -> TVProfile:
    """
    TCL 7105X (7-Series QLED, Google TV) profile.

    Menu paths follow the Google TV interface shipped on the 7105X.
    The White Balance and Color Tuner menus are found under Advanced Picture;
    the Gamma control is a preset selector (1.8 / 2.0 / 2.2 / 2.4) with no
    per-point calibration.  White Balance uses a -50 to +50 scale (0 = neutral).
    """
    return TVProfile(
        name="TCL 7105X",
        short_name="tcl7105x",
        PICTURE_MODES={
            CalMode.SDR: "Movie (or Filmmaker Mode for the most D65-accurate starting point)",
            CalMode.HDR10: "Filmmaker Mode (HDR) — disables most processing by default",
            CalMode.DOLBY_VISION: "Dolby Vision Dark (for dark-room viewing)",
        },
        DISABLE_BEFORE_CAL=[
            ("Local Dimming", "Off (for measurement accuracy); Low for normal viewing"),
            ("Dynamic Contrast", "Off"),
            ("Noise Reduction (DNR)", "Off"),
            ("MPEG Noise Reduction", "Off"),
            ("Motion Clarity (Action Smoothing)", "Off"),
            ("Game Mode", "Off (unless calibrating for a game input)"),
            ("Ambient Light Detection", "Off"),
            (
                "Dynamic Tone Mapping",
                "Off for SDR; leave on ST.2084 for HDR10 measurement",
            ),
        ],
        CONFIGURE_FOR_CAL=[
            ("Backlight", "Adjust to reach target peak luminance"),
            ("Brightness", "50 (factory default for Movie mode)"),
            (
                "Contrast",
                "90 (factory default for Movie mode; back down if 100% whites clip)",
            ),
            ("Color Temperature", "Warm (closest to D65 on this panel)"),
            (
                "Color Space",
                "Auto for SDR Rec.709; Native or Wide for HDR gamut measurement",
            ),
            (
                "Gamma",
                "2.2 for SDR (BT.1886 is not offered; 2.2 is the closest equivalent)",
            ),
            (
                "HDMI Mode",
                "Enhanced Signal (Settings → TV Inputs → HDMI) for 4K/HDR sources",
            ),
        ],
        PICTURE_CONTROL_GUIDE=[
            (
                "Brightness (Black Level)",
                "Show a 5% gray patch. Raise Brightness until 5% gray is just visible, then lower one click if blacks look lifted.",
            ),
            (
                "Contrast (White Level)",
                "Show a 95% white patch. Raise Contrast until near-white detail begins to clip, then back off 1-2 clicks.",
            ),
            (
                "Backlight",
                "Show a 100% white patch. Use Backlight to hit the target luminance (e.g. 120 nits for SDR).",
            ),
        ],
        AUTO_PRECAL_INTERVAL_MS=500,
        WB_MENU_PATH="Settings → Picture → Advanced Picture → White Balance",
        WB_2POINT={
            "Red Gain": "Adjusts red in highlights (80% gray patch) — scale −50 to +50, neutral 0",
            "Green Gain": "Adjusts green in highlights — scale −50 to +50, neutral 0",
            "Blue Gain": "Adjusts blue in highlights — scale −50 to +50, neutral 0",
            "Red Offset": "Adjusts red in shadows (30% gray patch) — scale −50 to +50, neutral 0",
            "Green Offset": "Adjusts green in shadows — scale −50 to +50, neutral 0",
            "Blue Offset": "Adjusts blue in shadows — scale −50 to +50, neutral 0",
        },
        WB_NOTES=[
            "Open Advanced Picture → White Balance.  The scale is −50 to +50; 0 is neutral.",
            "Measure 80% gray first and use the Gain controls only.  Do not touch Offset until Gain is close.",
            "After Gain is settled, switch to a 30% gray patch and adjust Offset.",
            "Changes interact: lowering Red Gain shifts x left on the CIE chart; lowering Blue Gain shifts it right.",
        ],
        GAMMA_MENU_PATH="Settings → Picture → Advanced Picture → Gamma",
        gamma_note=(
            "The TCL 7105X offers numbered presets: 1.8, 2.0, 2.2, 2.4. "
            "Select 2.2 for SDR — BT.1886 is not available by name on this model. "
            "There is no per-point gamma calibration; choose the preset that yields the closest average gamma across 20/40/60/80% gray."
        ),
        GAMMA_NOTES=[
            "Select 2.2 for SDR calibration.  In HDR10 and Dolby Vision modes, gamma is governed by PQ (ST.2084) and cannot be changed.",
            "Since only presets are available, measure 20/40/60/80% gray to find the average effective gamma, then pick the nearest preset.",
            "If the average is slightly above 2.2 (image too dark), try 2.0.  If it is below 2.2 (image too bright), try 2.4.",
            "After changing the preset, re-run the full 20/40/60/80% pass before declaring gamma done.",
        ],
        CMS_MENU_PATH="Settings → Picture → Advanced Picture → Color Tuner",
        CMS_CONTROLS=["Hue", "Saturation", "Brightness"],
        CMS_COLOURS=["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"],
        CMS_NOTES=[
            "Open Advanced Picture → Color Tuner and select the colour to adjust.",
            "Measure a fully saturated 100% colour patch (e.g. pure Red 255,0,0) while adjusting that colour entry.",
            "Hue rotates the measured point around the white point on the CIE chart.  Saturation moves it toward or away from white.",
            "Brightness in Color Tuner controls per-colour luminance — adjust it last, after Hue and Saturation are close.",
        ],
        RESET_BEFORE_CAL=[
            (
                "White Balance — Red/Green/Blue Gain",
                "0  (neutral on the −50 to +50 scale)",
            ),
            (
                "White Balance — Red/Green/Blue Offset",
                "0  (neutral on the −50 to +50 scale)",
            ),
            ("Color Tuner — Hue (all colours)", "0  (no colour rotation)"),
            ("Color Tuner — Saturation (all colours)", "0  (no saturation push/pull)"),
            (
                "Color Tuner — Brightness (all colours)",
                "0  (no per-colour luminance offset)",
            ),
            ("Brightness (black level)", "50 (Movie mode factory default)"),
            (
                "Contrast (white level)",
                "90 (Movie mode factory default; prevents highlight clipping)",
            ),
            (
                "Sharpness",
                "0  (no edge enhancement — sharpening corrupts near-white patch readings)",
            ),
        ],
        supported_gamuts=["bt709", "p3d65", "bt2020"],
    )


def _build_lg_oled55b7a_profile() -> TVProfile:
    """
    LG OLED55B7A (2017 B7 OLED, webOS 3.5) profile.

    The B7A uses LG's ISF Expert modes for calibration.  White-balance and
    CMS controls are accessible via the Picture → Expert Controls menu once
    an ISF Expert picture mode is selected.
    """
    return TVProfile(
        name="LG OLED55B7A",
        short_name="lg_oled55b7a",
        PICTURE_MODES={
            CalMode.SDR: "ISF Expert (Dark Room) for dark rooms, ISF Expert (Bright Room) for lit rooms",
            CalMode.HDR10: "Cinema (HDR) or ISF Expert (Dark Room) (HDR)",
            CalMode.DOLBY_VISION: "Dolby Vision Cinema",
        },
        DISABLE_BEFORE_CAL=[
            ("Dynamic Contrast", "Off"),
            ("Super Resolution", "Off"),
            ("Motion Eye Care", "Off"),
            ("TruMotion", "Off (De-Judder 0, De-Blur 0)"),
            ("Noise Reduction", "Off"),
            ("MPEG Noise Reduction", "Off"),
            ("Real Cinema", "On (24p content) or Off (for measurement)"),
            ("Dynamic Colour", "Off"),
            ("Colour Gamut", "Auto or Wide (not Extended)"),
            ("Eye Comfort Mode", "Off"),
        ],
        CONFIGURE_FOR_CAL=[
            ("OLED Light", "Adjust to target luminance (e.g. 35-45 for ~120 nits SDR)"),
            ("Contrast", "85-90 (avoid clipping highlights)"),
            ("Brightness", "50 (default)"),
            ("Colour Temperature", "Warm2 (closest to D65)"),
            ("Gamma", "BT.1886 for SDR (or 2.2)"),
            ("Colour Gamut", "Auto for SDR, Wide for HDR"),
            ("HDMI Ultra HD Deep Colour", "On (for HDR/4K sources)"),
        ],
        PICTURE_CONTROL_GUIDE=[
            (
                "Brightness (Black Level)",
                "Show a 5% gray patch. Raise Brightness until 5% is just barely visible, then lower one click if blacks look lifted. OLED blacks are self-emissive — even a small positive offset is visible.",
            ),
            (
                "Contrast (White Level)",
                "Show a 95% white patch. Raise Contrast until near-white detail begins to clip, then lower 1-2 clicks. On OLED, 85-90 is a typical safe upper limit.",
            ),
            (
                "OLED Light",
                "Show a 100% white patch and use OLED Light to hit the target luminance (e.g. ~120 nits SDR at OLED Light 35-45).",
            ),
        ],
        AUTO_PRECAL_INTERVAL_MS=500,
        WB_MENU_PATH="Settings → Picture → Picture Mode Settings → Expert Controls → White Balance",
        WB_2POINT={
            "Red Gain": "Adjusts red in highlights (80% gray) — scale −50 to +50, neutral 0",
            "Green Gain": "Adjusts green in highlights — scale −50 to +50, neutral 0",
            "Blue Gain": "Adjusts blue in highlights — scale −50 to +50, neutral 0",
            "Red Offset": "Adjusts red in shadows (30% gray) — scale −50 to +50, neutral 0",
            "Green Offset": "Adjusts green in shadows — scale −50 to +50, neutral 0",
            "Blue Offset": "Adjusts blue in shadows — scale −50 to +50, neutral 0",
        },
        WB_NOTES=[
            "Select an ISF Expert picture mode before opening Expert Controls — WB controls are hidden in non-ISF modes.",
            "The B7A WB scale is −50 to +50; 0 is neutral.  Start with Gain controls using an 80% gray patch.",
            "Settle Gain first, then switch to a 30% gray patch and adjust Offset controls.",
            "Keep 'Colour Filter' Off during all white balance measurements — it tints the image for single-channel viewing.",
        ],
        GAMMA_MENU_PATH="Settings → Picture → Picture Mode Settings → Expert Controls → Gamma",
        gamma_note=(
            "The B7A offers gamma presets (1.9, 2.2, BT.1886).  Select BT.1886 for SDR "
            "calibration.  In HDR modes, gamma is fixed to PQ (ST.2084) and cannot be changed."
        ),
        GAMMA_NOTES=[
            "Select BT.1886 for SDR — it targets 2.4 in a dark room and gracefully adapts to screen luminance.",
            "If BT.1886 is slightly too dark across the board, try 2.2.  There is no per-point gamma calibration on the B7A.",
            "In HDR10 and Dolby Vision modes, gamma is governed by PQ (ST.2084) — the Gamma control is greyed out.",
            "After selecting a preset, run the full 20/40/60/80% pass to verify tracking before proceeding.",
        ],
        CMS_MENU_PATH="Settings → Picture → Picture Mode Settings → Expert Controls → Colour Management System",
        CMS_CONTROLS=["Saturation", "Hue", "Luminance"],
        CMS_COLOURS=["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"],
        CMS_NOTES=[
            "Open Expert Controls → Colour Management System and select the colour to adjust.",
            "Measure a fully saturated 100% colour patch while adjusting that colour's entry.",
            "Hue rotates the point on the CIE chart; Saturation moves it toward or away from white; Luminance sets per-colour brightness.",
            "On the B7A, Saturation and Hue have larger effect than Luminance — adjust Hue first, then Saturation, then Luminance.",
        ],
        RESET_BEFORE_CAL=[
            (
                "White Balance — Red/Green/Blue Gain",
                "0  (LG scale is typically -50 to +50; 0 is neutral)",
            ),
            ("White Balance — Red/Green/Blue Offset", "0  (same scale; 0 is neutral)"),
            ("CMS — Saturation (all colours)", "0  (no saturation offset)"),
            ("CMS — Hue (all colours)", "0  (no hue rotation)"),
            ("CMS — Luminance (all colours)", "0  (no per-colour luminance shift)"),
            (
                "Brightness (black level)",
                "50 (LG ISF Expert default; prevents black crush or lift)",
            ),
            (
                "Contrast (white level)",
                "85 (ISF Expert Dark Room default; avoids highlight clip on OLED)",
            ),
            (
                "Sharpness",
                "0  (OLED panels are already razor-sharp; any sharpening distorts edge patches)",
            ),
            (
                "Colour Filter",
                "Off (must be off; it tints the entire image for colour-channel isolation)",
            ),
        ],
        llm_schema={
            "model": "LG OLED55B7A",
            "variants": ["OLED55B7A", "OLED65B7A"],
            "nomenclature": {
                "backlight": {
                    "term": "OLED Light",
                    "function": "Controls panel peak luminance. Sets overall brightness (nits). Does NOT affect black level.",
                    "path": "Settings > Picture > OLED Light",
                    "range": "0-100",
                    "hex_mapping": "00-64",
                },
                "black_level": {
                    "term": "Brightness",
                    "function": "Black level control. Sets the point where shadow detail clips or crushes. Calibration: set so near-black is just visible.",
                    "path": "Settings > Picture > Brightness",
                    "range": "0-100",
                    "hex_mapping": "00-64",
                },
            },
            "settings": {
                "white_balance_2pt": {
                    "path": "Settings > Picture > Picture Mode Settings > Expert Controls > White Balance > 2 Point",
                    "type": "slider",
                    "channels": {
                        "R_gain": {
                            "label": "Red Gain",
                            "affects": "red highlights (80% gray)",
                            "range": "-50 to +50",
                            "neutral": 0,
                        },
                        "G_gain": {
                            "label": "Green Gain",
                            "affects": "green highlights (80% gray)",
                            "range": "-50 to +50",
                            "neutral": 0,
                        },
                        "B_gain": {
                            "label": "Blue Gain",
                            "affects": "blue highlights (80% gray)",
                            "range": "-50 to +50",
                            "neutral": 0,
                        },
                        "R_offset": {
                            "label": "Red Offset",
                            "affects": "red shadows (30% gray)",
                            "range": "-50 to +50",
                            "neutral": 0,
                        },
                        "G_offset": {
                            "label": "Green Offset",
                            "affects": "green shadows (30% gray)",
                            "range": "-50 to +50",
                            "neutral": 0,
                        },
                        "B_offset": {
                            "label": "Blue Offset",
                            "affects": "blue shadows (30% gray)",
                            "range": "-50 to +50",
                            "neutral": 0,
                        },
                    },
                    "neutral_value": 0,
                    "increment": 1,
                    "note": "Use 80% gray for Gain adjustments, 30% gray for Offset adjustments. Select ISF Expert mode first.",
                },
                "white_balance_20pt": {
                    "path": "Settings > Picture > Picture Mode Settings > Expert Controls > White Balance > 20 Point",
                    "type": "multipoint",
                    "points": 20,
                    "target_nits": [540, 1000, 4000],
                    "neutral_value": 0,
                    "note": "Fine grayscale. Only use after 2-point is already close. Maps to HDR content targets.",
                },
                "cms": {
                    "path": "Settings > Picture > Picture Mode Settings > Expert Controls > Colour Management System",
                    "type": "cms",
                    "colors": ["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"],
                    "params": {
                        "Hue": {
                            "range": "Red 00 to Green 64",
                            "neutral": 0,
                            "affects": "colour angle on CIE chart",
                        },
                        "Saturation": {
                            "range": "0-100",
                            "neutral": 0,
                            "affects": "colour purity/vibrancy",
                        },
                        "Luminance": {
                            "range": "0-100",
                            "neutral": 0,
                            "affects": "per-colour brightness",
                        },
                    },
                    "neutral_value": 0,
                    "increment": 1,
                    "note": "Adjust Hue first, then Saturation, then Luminance. Set CMY to Null/bypass before 3D LUT.",
                },
                "contrast": {
                    "path": "Settings > Picture > Contrast",
                    "type": "slider",
                    "range": "0-100",
                    "hex_mapping": "00-64",
                    "increment": 1,
                    "calibration_default": 85,
                    "note": "Controls peak white. 85-90 typical safe limit on OLED. Back down if 95% clips.",
                },
                "brightness": {
                    "path": "Settings > Picture > Brightness",
                    "type": "slider",
                    "range": "0-100",
                    "hex_mapping": "00-64",
                    "increment": 1,
                    "calibration_default": 50,
                    "note": "Black level. Set so 5% near-black is just visible. Default 50 prevents crush or lift.",
                },
                "color": {
                    "path": "Settings > Picture > Color",
                    "type": "slider",
                    "range": "0-100",
                    "hex_mapping": "00-64",
                    "increment": 1,
                    "note": "Colour saturation control.",
                },
                "tint": {
                    "path": "Settings > Picture > Tint",
                    "type": "slider",
                    "range": "Red 00 to Green 64",
                    "hex_mapping": "00-64",
                    "increment": 1,
                    "note": "Green-Red balance.",
                },
                "sharpness": {
                    "path": "Settings > Picture > Sharpness",
                    "type": "slider",
                    "range": "0-50",
                    "hex_mapping": "00-32",
                    "increment": 1,
                    "calibration_default": 0,
                    "note": "Set to 0. OLED is already razor-sharp; sharpening distorts measurements.",
                },
                "color_temperature": {
                    "path": "Settings > Picture > Color Temperature",
                    "type": "slider",
                    "range": "0-100",
                    "hex_mapping": "00-64",
                    "increment": 1,
                    "calibration_target": "Warm2 (closest to D65)",
                    "note": "Higher = warmer (more red).",
                },
                "gamma": {
                    "path": "Settings > Picture > Picture Mode Settings > Expert Controls > Gamma",
                    "type": "enum",
                    "options": [1.9, 2.2, "BT.1886"],
                    "default": "BT.1886",
                    "calibration_target": "BT.1886",
                    "note": "In HDR modes, gamma is fixed to PQ (ST-2084) and cannot be changed.",
                },
                "dynamic_contrast": {
                    "path": "Settings > Picture > Picture Mode Settings > Expert Controls > Dynamic Contrast",
                    "type": "enum",
                    "options": ["Off", "Low", "Medium", "High"],
                    "calibration_value": "Off",
                    "note": "Disable during calibration. Enable Low for accurate HDR after.",
                },
                "edge_enhancer": {
                    "path": "Settings > Picture > Picture Mode Settings > Expert Controls > Edge Enhancer",
                    "type": "toggle",
                    "options": ["On", "Off"],
                    "note": "Default On acts as bypass. Leave On for accurate measurements.",
                },
                "active_hdr": {
                    "path": "Settings > Picture > Picture Mode Settings > Dynamic Contrast",
                    "alias": "dynamic_contrast",
                    "note": "Same control in HDR modes. Set Off for calibration, Low for accurate HDR playback.",
                },
            },
        },
    )


def _build_vizio_v4k55m_profile() -> TVProfile:
    """
    Vizio V4K55M-0801 (V-Series 2024, 55″ 4K) profile.

    The V-Series is a budget model with limited user-facing calibration
    controls.  However, a hidden *service menu* exposes factory white-balance
    Gain/Offset and per-channel Color Tuner settings that are not available
    through the normal picture settings UI.

    ⚠ The service menu is intended for technicians — incorrect changes can
    brick the TV.  Proceed at your own risk.
    """
    return TVProfile(
        name="Vizio V4K55M-0801",
        short_name="vizio_v4k55m",
        PICTURE_MODES={
            CalMode.SDR: "Calibrated Dark (or Calibrated for bright rooms)",
            CalMode.HDR10: "Calibrated Dark (HDR)",
            CalMode.DOLBY_VISION: "Dolby Vision Dark",
        },
        DISABLE_BEFORE_CAL=[
            ("Active Full Array (Local Dimming)", "Off (for measurement)"),
            ("Motion Smoothing / Clear Action", "Off"),
            ("Ambient Light Sensor", "Off"),
            ("Reduce Noise", "Off"),
            ("Reduce Signal Noise (MPEG NR)", "Off"),
            ("Game Mode", "Off (unless calibrating for game input)"),
            ("Film Mode", "Off"),
            ("Enhanced Viewing Angle", "Off (if available)"),
        ],
        CONFIGURE_FOR_CAL=[
            ("Backlight", "Adjust to target luminance"),
            ("Brightness", "50 (default)"),
            ("Contrast", "50 (default)"),
            ("Color Temperature", "Warm (closest to D65)"),
            ("Gamma", "2.2 for SDR (or 2.4 for dark room)"),
            ("Color Space", "Auto"),
            ("HDMI Mode", "2.1 / Enhanced (for HDR/4K sources)"),
        ],
        PICTURE_CONTROL_GUIDE=[
            (
                "Brightness (Black Level)",
                "Show a 5% gray patch. Raise Brightness until 5% is just barely visible, then lower one click if blacks look elevated.",
            ),
            (
                "Contrast (White Level)",
                "Show a 95% white patch. Raise Contrast until detail starts to clip, then back off 1-2 clicks. Default of 50 is usually safe.",
            ),
            (
                "Backlight",
                "Show a 100% white patch. Use Backlight to reach the target luminance (e.g. 120 nits SDR).",
            ),
        ],
        AUTO_PRECAL_INTERVAL_MS=0,
        WB_MENU_PATH=(
            "Service Menu → White Balance (see service menu access below) "
            "— or — Settings → Picture → Color Calibration → Color Tuner"
        ),
        WB_2POINT={
            "Red Gain": "Adjusts red in highlights — found in service menu White Balance section",
            "Green Gain": "Adjusts green in highlights — service menu",
            "Blue Gain": "Adjusts blue in highlights — service menu",
            "Red Offset": "Adjusts red in shadows — service menu",
            "Green Offset": "Adjusts green in shadows — service menu",
            "Blue Offset": "Adjusts blue in shadows — service menu",
        },
        WB_NOTES=[
            "The V-Series does not expose Gain/Offset white balance in its normal menus — use the service menu (see Prepare TV for access instructions).",
            "Write down every original service menu value before changing anything.  Incorrect entries can brick the panel.",
            "Use 80% gray for Gain adjustments and 30% gray for Offset.  Settle Gain before touching Offset.",
            "After leaving the service menu, verify the picture mode is still set to Calibrated Dark before re-measuring.",
        ],
        GAMMA_MENU_PATH="Settings → Picture → Advanced Picture → Gamma",
        gamma_note=(
            "The V4K55M offers gamma presets (1.8, 2.0, 2.2, 2.4).  "
            "Select 2.2 for SDR calibration.  In HDR modes, gamma follows "
            "PQ (ST.2084) automatically."
        ),
        GAMMA_NOTES=[
            "Select 2.2 for SDR — there is no BT.1886 option by name on this model.",
            "Since only presets are available, measure 20/40/60/80% gray to find the average effective gamma, then pick the nearest preset.",
            "If the average is slightly above 2.2 (image too dark), try 2.0.  If it is below 2.2 (too bright), try 2.4.",
            "In HDR10 and Dolby Vision modes, gamma is set by PQ (ST.2084) and cannot be adjusted.",
        ],
        CMS_MENU_PATH=(
            "Service Menu → Color Tuner (see service menu access below) "
            "— or — Settings → Picture → Color Calibration → Color Tuner"
        ),
        CMS_CONTROLS=["Hue", "Saturation", "Brightness"],
        CMS_COLOURS=["Red", "Green", "Blue", "Cyan", "Magenta", "Yellow"],
        CMS_NOTES=[
            "The service menu Color Tuner offers finer resolution than the user-menu Color Calibration controls — prefer it when available.",
            "Measure a fully saturated 100% colour patch while adjusting that colour's entry.",
            "Adjust Hue first to fix the chromaticity angle, then Saturation, then Brightness for luminance.",
            "Exit the service menu carefully via HOME / EXIT — do not power-cycle mid-adjustment.",
        ],
        RESET_BEFORE_CAL=[
            (
                "White Balance — Red/Green/Blue Gain (service menu)",
                "Default/factory value — write down originals before changing anything",
            ),
            (
                "White Balance — Red/Green/Blue Offset (service menu)",
                "Default/factory value — write down originals before changing anything",
            ),
            ("Color Tuner — Hue (all colours)", "0  (user menu or service menu)"),
            ("Color Tuner — Saturation (all colours)", "0"),
            ("Color Tuner — Brightness (all colours)", "0"),
            ("Brightness (black level)", "50 (factory default)"),
            ("Contrast (white level)", "50 (factory default)"),
            ("Sharpness", "0  (no edge enhancement)"),
            (
                "Color Enhancement / Color AI",
                "Off — this is separate from the disable list and easy to miss",
            ),
        ],
        SERVICE_MENU_ACCESS=(
            "With the TV on, press MENU on the Vizio remote, then enter "
            "1-9-9-9, then press BACK.  The service menu will appear.  "
            "Exit by pressing the EXIT / HOME button.\n"
            "⚠ WARNING: The service menu is for factory technicians.  "
            "Changing the wrong setting can brick the TV.  Write down every "
            "original value before you change anything."
        ),
        SERVICE_MENU_CONTROLS=[
            (
                "White Balance → Red/Green/Blue Gain",
                "Fine-tune highlight white balance (not exposed in normal menus on V-Series)",
            ),
            ("White Balance → Red/Green/Blue Offset", "Fine-tune shadow white balance"),
            (
                "Color Tuner → per-colour Hue/Sat/Brightness",
                "Per-colour CMS adjustments with finer resolution than user menu",
            ),
            (
                "Panel Calibration → Uniformity",
                "Factory uniformity correction (do not change unless you know what you are doing)",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# TV Profile registry
# ---------------------------------------------------------------------------

TV_PROFILES: Dict[str, TVProfile] = {
    "u8g": _build_u8g_profile(),
    "tcl7105x": _build_tcl7105x_profile(),
    "lg_oled55b7a": _build_lg_oled55b7a_profile(),
    "vizio_v4k55m": _build_vizio_v4k55m_profile(),
}

DEFAULT_TV_PROFILE = "u8g"


def get_tv_profile(model_name: str) -> Optional[TVProfile]:
    """Return the TVProfile for the given model key, or None if not found.

    Accepts the short_name key used in TV_PROFILES (e.g. "u8g", "tcl7105x").
    """
    return TV_PROFILES.get(model_name)
