"""Tests for TV profile registry and structure."""
import pytest

from calibrator import (
    TV_PROFILES,
    TVProfile,
    CalMode,
    DEFAULT_TV_PROFILE,
)


class TestProfileRegistry:
    def test_all_expected_profiles_registered(self):
        expected = {"u8g", "tcl7105x", "lg_oled55b7a", "vizio_v4k55m"}
        assert set(TV_PROFILES.keys()) == expected

    def test_default_profile_exists(self):
        assert DEFAULT_TV_PROFILE in TV_PROFILES

    def test_all_profiles_are_tvprofile_instances(self):
        for key, profile in TV_PROFILES.items():
            assert isinstance(profile, TVProfile), f"{key} is not a TVProfile"


class TestProfileStructure:
    """Every profile must have required fields populated."""

    @pytest.fixture(params=list(TV_PROFILES.keys()))
    def profile(self, request):
        return TV_PROFILES[request.param]

    def test_has_name(self, profile):
        assert profile.name, "name must be non-empty"

    def test_has_short_name(self, profile):
        assert profile.short_name, "short_name must be non-empty"

    def test_short_names_are_unique(self):
        short_names = [p.short_name for p in TV_PROFILES.values()]
        assert len(short_names) == len(set(short_names))

    def test_picture_modes_for_all_cal_modes(self, profile):
        for mode in CalMode:
            assert mode in profile.PICTURE_MODES, (
                f"{profile.name} missing PICTURE_MODES[{mode}]"
            )
            assert profile.PICTURE_MODES[mode], (
                f"{profile.name} has empty PICTURE_MODES[{mode}]"
            )

    def test_has_disable_settings(self, profile):
        assert len(profile.DISABLE_BEFORE_CAL) > 0

    def test_has_configure_settings(self, profile):
        assert len(profile.CONFIGURE_FOR_CAL) > 0

    def test_has_wb_menu_path(self, profile):
        assert profile.WB_MENU_PATH

    def test_has_wb_controls(self, profile):
        assert len(profile.WB_2POINT) >= 6, "Need at least 6 WB controls (RGB Gain + Offset)"

    def test_has_gamma_menu_path(self, profile):
        assert profile.GAMMA_MENU_PATH

    def test_has_cms_menu_path(self, profile):
        assert profile.CMS_MENU_PATH

    def test_has_cms_controls(self, profile):
        assert len(profile.CMS_CONTROLS) >= 2

    def test_has_cms_colours(self, profile):
        assert len(profile.CMS_COLOURS) >= 6
        for c in ["Red", "Green", "Blue"]:
            assert c in profile.CMS_COLOURS


class TestServiceMenuField:
    """Service menu fields should be present on Vizio, absent/empty on others."""

    def test_vizio_has_service_menu(self):
        p = TV_PROFILES["vizio_v4k55m"]
        assert p.SERVICE_MENU_ACCESS
        assert len(p.SERVICE_MENU_CONTROLS) > 0

    def test_u8g_no_service_menu(self):
        p = TV_PROFILES["u8g"]
        assert not p.SERVICE_MENU_ACCESS
        assert len(p.SERVICE_MENU_CONTROLS) == 0

    def test_service_menu_fields_default_empty(self):
        """A bare TVProfile should have empty service menu fields."""
        p = TVProfile()
        assert p.SERVICE_MENU_ACCESS == ""
        assert p.SERVICE_MENU_CONTROLS == []
