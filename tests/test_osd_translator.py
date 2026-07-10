"""
Tests for the OSD Command Translator (#167).

Covers:
- Adjustment normalisation from LLM format
- White balance step generation for all 4 TV profiles
- CMS step generation for all 4 TV profiles
- Gamma step generation
- Picture control step generation (backlight, contrast, etc.)
- ADB skip detection for U8G
- Skipped corrections with reasons
- Empty/invalid input handling
- tv_key field in result
"""

import pytest

from calibrator.osd import (
    OSDStep,
    OSDTranslationResult,
    _normalise_adjustment,
    _derive_setting_key,
    _compute_delta,
    _format_value,
    _format_delta,
    _build_menu_path,
    _extract_cms_colour,
    _generate_wb_steps,
    _generate_cms_steps,
    _generate_gamma_steps,
    _generate_picture_control_steps,
    _generate_steps_for_adjustment,
    translate_corrections,
    translate_from_adjustment_plan,
)
from calibrator.profiles import TV_PROFILES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

U8G = TV_PROFILES["u8g"]
TCL = TV_PROFILES["tcl7105x"]
LG = TV_PROFILES["lg_oled55b7a"]
VIZIO = TV_PROFILES["vizio_v4k55m"]

ALL_PROFILES = [U8G, TCL, LG, VIZIO]


def _llm_adj(setting, menu, from_val=None, to_val=None, reason=""):
    """Build a minimal LLM-style adjustment dict."""
    return {
        "setting": setting,
        "menu": menu,
        "from": from_val,
        "to": to_val,
        "reason": reason,
        "scope": "local",
    }


def _canonical_adj(setting_key, from_val=None, to_val=None, delta=None, reason=""):
    """Build a canonical adjustment dict (already normalised)."""
    return {
        "setting_key": setting_key,
        "from": from_val,
        "to": to_val,
        "delta": delta,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


class TestNormalisation:
    def test_derive_setting_key_red_gain(self):
        assert _derive_setting_key("R-Gain", "") == "white_balance_2pt.channels.R_gain"

    def test_derive_setting_key_blue_offset(self):
        assert _derive_setting_key("B-Offset", "") == "white_balance_2pt.channels.B_offset"

    def test_derive_setting_key_saturation(self):
        assert _derive_setting_key("Saturation", "") == "cms.params.Saturation"

    def test_derive_setting_key_gamma(self):
        assert _derive_setting_key("Gamma", "") == "gamma"

    def test_derive_setting_key_backlight(self):
        assert _derive_setting_key("Backlight", "") == "backlight"

    def test_derive_setting_key_fallback(self):
        result = _derive_setting_key("Some Weird Setting", "")
        assert result == "some_weird_setting"

    def test_compute_delta_positive(self):
        assert _compute_delta(0, 3) == 3.0

    def test_compute_delta_negative(self):
        assert _compute_delta(3, 0) == -3.0

    def test_compute_delta_none_from(self):
        assert _compute_delta(None, 3) is None

    def test_compute_delta_none_to(self):
        assert _compute_delta(0, None) is None

    def test_format_value_integer(self):
        assert _format_value(3.0, "slider") == "3"

    def test_format_value_float(self):
        assert _format_value(2.28, "slider") == "2.28"

    def test_format_value_enum(self):
        assert _format_value("BT.1886", "enum") == "BT.1886"

    def test_format_value_none(self):
        assert _format_value(None, "slider") is None

    def test_format_delta_positive(self):
        assert _format_delta(3.0, "slider") == "+3"

    def test_format_delta_negative(self):
        assert _format_delta(-2.0, "slider") == "-2"

    def test_format_delta_float(self):
        assert _format_delta(0.5, "slider") == "+0.5"

    def test_build_menu_path_with_channel(self):
        result = _build_menu_path("Settings > Picture", "Red")
        assert "Red" in result

    def test_build_menu_path_no_channel(self):
        result = _build_menu_path("Settings > Picture")
        assert "Settings" in result

    def test_extract_cms_colour_from_field(self):
        assert _extract_cms_colour({"colour": "Red"}) == "Red"

    def test_extract_cms_colour_fallback(self):
        assert _extract_cms_colour({"setting": "Hue"}) == "Red"


# ---------------------------------------------------------------------------
# Adjustment normalisation
# ---------------------------------------------------------------------------


class TestAdjustmentNormalisation:
    def test_normalise_from_llm_format(self):
        adj = _normalise_adjustment(_llm_adj("R-Gain", "", 0, 3))
        assert adj["setting_key"] == "white_balance_2pt.channels.R_gain"
        assert adj["to"] == 3
        assert adj["delta"] == 3.0

    def test_normalise_already_canonical(self):
        adj = _normalise_adjustment(_canonical_adj("gamma", to_val=2.2))
        assert adj["setting_key"] == "gamma"
        assert adj["to"] == 2.2

    def test_normalise_preserves_reason(self):
        adj = _normalise_adjustment(_llm_adj("Gamma", "", reason="Gamma too dark"))
        assert adj["reason"] == "Gamma too dark"


# ---------------------------------------------------------------------------
# White balance step generation
# ---------------------------------------------------------------------------


class TestWBSteps:
    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_generate_wb_steps_has_menu_path(self, profile):
        adj = _canonical_adj("white_balance_2pt.channels.R_gain", from_val=0, to_val=3)
        steps = _generate_wb_steps(profile, adj)
        assert len(steps) >= 1
        assert steps[0].menu_path
        assert steps[0].action

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_generate_wb_steps_gain(self, profile):
        adj = _canonical_adj("white_balance_2pt.channels.R_gain", from_val=0, to_val=3)
        steps = _generate_wb_steps(profile, adj)
        assert any("Red" in s.menu_path for s in steps)

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_generate_wb_steps_offset(self, profile):
        adj = _canonical_adj("white_balance_2pt.channels.B_offset", from_val=0, to_val=-2)
        steps = _generate_wb_steps(profile, adj)
        assert any("Blue" in s.menu_path for s in steps)

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_generate_wb_steps_value_display(self, profile):
        adj = _canonical_adj("white_balance_2pt.channels.G_gain", to_val=5)
        steps = _generate_wb_steps(profile, adj)
        assert steps[0].value == "5"

    def test_u8g_wb_ire_points(self):
        adj = _canonical_adj("white_balance_2pt.channels.R_gain", to_val=3)
        steps = _generate_wb_steps(U8G, adj)
        assert any("IRE 80" in s.menu_path or "IRE 20" in s.menu_path for s in steps)

    def test_tcl_wb_with_menu_path(self):
        adj = _canonical_adj("white_balance_2pt.channels.B_gain", to_val=-3)
        steps = _generate_wb_steps(TCL, adj)
        assert len(steps) >= 1
        assert "Blue" in steps[0].menu_path

    def test_lg_wb_with_menu_path(self):
        adj = _canonical_adj("white_balance_2pt.channels.R_offset", to_val=2)
        steps = _generate_wb_steps(LG, adj)
        assert len(steps) >= 1
        assert "Red" in steps[0].menu_path

    def test_vizio_wb_with_menu_path(self):
        adj = _canonical_adj("white_balance_2pt.channels.G_offset", to_val=-1)
        steps = _generate_wb_steps(VIZIO, adj)
        assert len(steps) >= 1
        assert "Green" in steps[0].menu_path


# ---------------------------------------------------------------------------
# CMS step generation
# ---------------------------------------------------------------------------


class TestCMSSteps:
    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_generate_cms_steps_has_path(self, profile):
        adj = _canonical_adj("cms.params.Saturation", to_val=2)
        adj["colour"] = "Red"
        steps = _generate_cms_steps(profile, adj)
        assert len(steps) >= 1
        assert steps[0].menu_path

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_generate_cms_steps_includes_colour(self, profile):
        adj = _canonical_adj("cms.params.Hue", to_val=1)
        adj["colour"] = "Green"
        steps = _generate_cms_steps(profile, adj)
        assert any("Green" in s.menu_path for s in steps)

    def test_u8g_cms_steps(self):
        adj = _canonical_adj("cms.params.Brightness", to_val=3)
        adj["colour"] = "Blue"
        steps = _generate_cms_steps(U8G, adj)
        assert any("Blue" in s.menu_path for s in steps)
        assert any("Brightness" in s.action for s in steps)

    def test_lg_cms_steps(self):
        adj = _canonical_adj("cms.params.Luminance", to_val=-2)
        adj["colour"] = "Cyan"
        steps = _generate_cms_steps(LG, adj)
        assert any("Cyan" in s.menu_path for s in steps)


# ---------------------------------------------------------------------------
# Gamma step generation
# ---------------------------------------------------------------------------


class TestGammaSteps:
    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_generate_gamma_steps(self, profile):
        adj = _canonical_adj("gamma", to_val="BT.1886")
        steps = _generate_gamma_steps(profile, adj)
        assert len(steps) >= 1
        assert steps[0].action == "Set gamma preset"

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_generate_gamma_steps_value(self, profile):
        adj = _canonical_adj("gamma", to_val=2.2)
        steps = _generate_gamma_steps(profile, adj)
        assert steps[0].value == "2.2"

    def test_gamma_steps_with_delta(self):
        adj = _canonical_adj("gamma", delta=0.1)
        steps = _generate_gamma_steps(U8G, adj)
        assert steps[0].value == "+0.1"


# ---------------------------------------------------------------------------
# Picture control step generation
# ---------------------------------------------------------------------------


class TestPictureControlSteps:
    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_backlight_steps(self, profile):
        adj = _canonical_adj("backlight", to_val=60)
        steps = _generate_picture_control_steps(profile, adj)
        assert len(steps) >= 1
        assert "Backlight" in steps[0].action or "backlight" in steps[0].menu_path.lower()

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_contrast_steps(self, profile):
        adj = _canonical_adj("contrast", to_val=85)
        steps = _generate_picture_control_steps(profile, adj)
        assert len(steps) >= 1
        assert "Contrast" in steps[0].action

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_black_level_steps(self, profile):
        adj = _canonical_adj("black_level", to_val=50)
        steps = _generate_picture_control_steps(profile, adj)
        assert len(steps) >= 1


# ---------------------------------------------------------------------------
# Step routing
# ---------------------------------------------------------------------------


class TestStepRouting:
    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_route_wb(self, profile):
        adj = _canonical_adj("white_balance_2pt.channels.R_gain", to_val=3)
        steps = _generate_steps_for_adjustment(profile, adj)
        assert len(steps) >= 1
        assert "Red" in steps[0].menu_path

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_route_cms(self, profile):
        adj = _canonical_adj("cms.params.Saturation", to_val=2)
        adj["colour"] = "Red"
        steps = _generate_steps_for_adjustment(profile, adj)
        assert len(steps) >= 1

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_route_gamma(self, profile):
        adj = _canonical_adj("gamma", to_val="BT.1886")
        steps = _generate_steps_for_adjustment(profile, adj)
        assert len(steps) >= 1

    @pytest.mark.parametrize("profile", ALL_PROFILES)
    def test_route_unknown_setting(self, profile):
        adj = _canonical_adj("some_unknown_setting", to_val=5)
        steps = _generate_steps_for_adjustment(profile, adj)
        assert len(steps) == 1
        # Setting name is title-cased for display
        assert "unknown setting" in steps[0].menu_path.lower()


# ---------------------------------------------------------------------------
# Full translation
# ---------------------------------------------------------------------------


class TestTranslateCorrections:
    def test_u8g_translation(self):
        adjustments = [
            _llm_adj("R-Gain", "", 0, 3, "Red highlights too warm"),
            _llm_adj("B-Offset", "", 0, -2, "Blue shadows too cool"),
            _llm_adj("Gamma", "", "2.2", "BT.1886"),
        ]
        result = translate_corrections(adjustments, U8G)
        assert result.tv_model == "Hisense U8G"
        assert result.tv_key == "u8g"
        assert len(result.steps) >= 3
        assert not result.skipped_reasons
        assert result.uses_adb is True

    def test_tcl_translation(self):
        adjustments = [
            _llm_adj("Red Gain", "", 0, 2),
            _llm_adj("Saturation", "", 0, 1),
        ]
        adjustments[1]["colour"] = "Green"
        result = translate_corrections(adjustments, TCL)
        assert result.tv_model == "TCL 7105X"
        assert result.tv_key == "tcl7105x"
        assert len(result.steps) >= 2

    def test_lg_translation(self):
        adjustments = [
            _llm_adj("Blue Gain", "", 0, 4, "Blue gain too low"),
        ]
        result = translate_corrections(adjustments, LG)
        assert result.tv_model == "LG OLED55B7A"
        assert result.tv_key == "lg_oled55b7a"
        assert len(result.steps) >= 1

    def test_vizio_translation(self):
        adjustments = [
            _llm_adj("Gamma", "", "2.2", "2.4"),
        ]
        result = translate_corrections(adjustments, VIZIO)
        assert result.tv_model == "Vizio V4K55M-0801"
        assert result.tv_key == "vizio_v4k55m"
        assert len(result.steps) >= 1

    def test_empty_adjustments(self):
        result = translate_corrections([], U8G)
        assert result.steps == []

    def test_skipped_unknown_setting(self):
        adjustments = [_llm_adj("NonExistentControl", "", 0, 5)]
        result = translate_corrections(adjustments, U8G)
        assert len(result.skipped_reasons) >= 1
        assert "NonExistentControl" in result.skipped_reasons[0]

    def test_one_step_per_adjustment(self):
        """Regression test for #605: each mappable adjustment must produce
        exactly one OSD step, not two, and step numbers must be unique."""
        adjustments = [
            _llm_adj("Red Gain", "", 0, -3, "x too high"),
            _llm_adj("Green Gain", "", 0, 2, "y too low"),
            _llm_adj("Blue Offset", "", 0, -1, "z shadows too cool"),
        ]
        result = translate_corrections(adjustments, U8G)
        assert not result.skipped_reasons
        assert len(result.steps) == len(adjustments)
        step_numbers = [step.step_number for step in result.steps]
        assert step_numbers == sorted(set(step_numbers))

    def test_step_to_dict(self):
        step = OSDStep(step_number=1, menu_path="A > B", action="Adjust X", value="5")
        d = step.to_dict()
        assert d["step_number"] == 1
        assert d["menu_path"] == "A > B"
        assert d["action"] == "Adjust X"
        assert d["value"] == "5"
        assert d["completed"] is False

    def test_result_to_dict(self):
        result = OSDTranslationResult(tv_model="Test", tv_key="test")
        result.steps = [
            OSDStep(step_number=1, menu_path="A", action="B"),
        ]
        result.skipped_reasons = ["reason1"]
        d = result.to_dict()
        assert d["tv_model"] == "Test"
        assert d["tv_key"] == "test"
        assert len(d["steps"]) == 1
        assert d["skipped_reasons"] == ["reason1"]


# ---------------------------------------------------------------------------
# translate_from_adjustment_plan wrapper
# ---------------------------------------------------------------------------


class TestTranslateFromAdjustmentPlan:
    def test_plan_with_adjustments(self):
        plan = {
            "adjustments": [
                _llm_adj("R-Gain", "", 0, 3),
                _llm_adj("Gamma", "", "2.2", "BT.1886"),
            ],
            "next_step": "proceed_cms",
            "confidence": 0.85,
        }
        result = translate_from_adjustment_plan(plan, U8G)
        assert len(result.steps) >= 2

    def test_plan_no_adjustments(self):
        plan = {"adjustments": [], "next_step": "verify", "confidence": 1.0}
        result = translate_from_adjustment_plan(plan, U8G)
        assert result.steps == []


# ---------------------------------------------------------------------------
# Rationale content
# ---------------------------------------------------------------------------


class TestRationale:
    def test_wb_rationale_includes_channel(self):
        adj = _canonical_adj("white_balance_2pt.channels.R_gain", to_val=3)
        steps = _generate_wb_steps(U8G, adj)
        assert "Red" in steps[0].rationale or "Gain" in steps[0].rationale

    def test_gamma_rationale_default(self):
        adj = _canonical_adj("gamma", to_val="BT.1886")
        steps = _generate_gamma_steps(U8G, adj)
        assert "tracking" in steps[0].rationale.lower()

    def test_custom_rationale_preserved(self):
        adj = _canonical_adj("gamma", to_val="BT.1886", reason="Midtones too bright")
        steps = _generate_gamma_steps(U8G, adj)
        assert "Midtones" in steps[0].rationale

    def test_adb_rationale_for_u8g(self):
        adjustments = [_llm_adj("R-Gain", "", 0, 3)]
        result = translate_corrections(adjustments, U8G)
        assert result.uses_adb is True
        assert all("ADB" in s.rationale for s in result.steps)

    def test_no_adb_for_other_tvs(self):
        adjustments = [_llm_adj("R-Gain", "", 0, 3)]
        result = translate_corrections(adjustments, TCL)
        assert result.uses_adb is False
