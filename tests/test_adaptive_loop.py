"""Tests for adaptive multi-pass loop (#166)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from calcore.llm import (
    ConvergenceAssessment,
    NextSettingsPrediction,
    PassDecision,
    _CONVERGENCE_STALL_EPSILON,
    assess_convergence,
    predict_next_settings,
    query_pass_decision,
)
from calcore.models import Summary
from calibrator.profiles import TV_PROFILES
from calibrator.session import (
    REPATCH_MAX_PASSES,
    session_view,
    serialize_session,
    deserialize_session,
    _repass_target_step,
    SessionStore,
)
from fastapi import HTTPException


class TestRepashMaxPasses:
    """REPATCH_MAX_PASSES constant."""

    def test_constant_is_three(self):
        assert REPATCH_MAX_PASSES == 3


class TestPassDecisionDataclass:
    """PassDecision dataclass structure."""

    def test_accept_decision(self):
        d = PassDecision(
            action="accept",
            patches=[],
            reason="All metrics within thresholds",
            confidence=0.95,
            repass_count=0,
        )
        assert d.action == "accept"
        assert d.patches == []
        assert d.ceiling_reason is None

    def test_repatch_decision(self):
        d = PassDecision(
            action="repatch",
            patches=["White 10%", "White 30%", "White 90%"],
            reason="Grayscale avg dE 2.5 exceeds threshold",
            confidence=0.8,
            repass_count=1,
        )
        assert d.action == "repatch"
        assert len(d.patches) == 3

    def test_ceiling_decision(self):
        d = PassDecision(
            action="ceiling",
            patches=[],
            reason="Cannot achieve target after 3 repasses",
            confidence=0.7,
            repass_count=3,
            ceiling_reason="Blue primary dE 7.2 exceeds hardware correction range",
        )
        assert d.action == "ceiling"
        assert d.ceiling_reason is not None


class TestQueryPassDecision:
    """query_pass_decision function."""

    def test_returns_none_without_llm_config(self):
        """Returns None when endpoint/model are missing."""
        llm = MagicMock()
        llm.endpoint = ""
        llm.model = ""
        result = query_pass_decision(
            measurements=[],
            phase="pre_grayscale",
            signal_range="full",
            code_scale="8bit",
            target_gamma=2.2,
            target_peak_nits=120.0,
            target_white_point=[0.3127, 0.3290],
            target_gamut="bt709",
            llm=llm,
        )
        assert result is None

    def test_returns_none_without_measurements(self):
        """Returns None when measurements list is empty."""
        llm = MagicMock()
        llm.endpoint = "http://localhost:4000"
        llm.model = "test-model"
        llm.api_key = ""
        llm.timeout = 30.0
        result = query_pass_decision(
            measurements=[],
            phase="pre_grayscale",
            signal_range="full",
            code_scale="8bit",
            target_gamma=2.2,
            target_peak_nits=120.0,
            target_white_point=[0.3127, 0.3290],
            target_gamut="bt709",
            llm=llm,
        )
        assert result is None

    def test_parses_accept_response(self):
        """Correctly parses LLM accept decision."""
        llm = MagicMock()
        llm.endpoint = "http://localhost:4000"
        llm.model = "test-model"
        llm.api_key = ""
        llm.timeout = 30.0

        mock_response = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "action": "accept",
                        "patches": [],
                        "reason": "All dE values within tolerance",
                        "confidence": 0.9,
                        "repass_count": 0,
                        "ceiling_reason": None,
                    })
                }
            }]
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None

            result = query_pass_decision(
                measurements=[
                    {"label": "White 50%", "delta_e": 1.2, "stimulus_pct": 50},
                    {"label": "White 80%", "delta_e": 1.5, "stimulus_pct": 80},
                ],
                phase="pre_grayscale",
                signal_range="full",
                code_scale="8bit",
                target_gamma=2.2,
                target_peak_nits=120.0,
                target_white_point=[0.3127, 0.3290],
                target_gamut="bt709",
                llm=llm,
                repass_count=0,
            )

        assert result is not None
        assert result.action == "accept"
        assert result.confidence == 0.9
        assert result.repass_count == 0

    def test_parses_repatch_response(self):
        """Correctly parses LLM repatch decision."""
        llm = MagicMock()
        llm.endpoint = "http://localhost:4000"
        llm.model = "test-model"
        llm.api_key = ""
        llm.timeout = 30.0

        mock_response = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "action": "repatch",
                        "patches": ["White 10%", "Cyan 75%"],
                        "reason": "Grayscale and color errors exceed thresholds",
                        "confidence": 0.85,
                        "repass_count": 1,
                        "ceiling_reason": None,
                    })
                }
            }]
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None

            result = query_pass_decision(
                measurements=[
                    {"label": "White 10%", "delta_e": 4.5, "stimulus_pct": 10},
                    {"label": "Cyan 75%", "delta_e": 5.0, "stimulus_pct": 75},
                ],
                phase="color_tuner",
                signal_range="full",
                code_scale="8bit",
                target_gamma=2.2,
                target_peak_nits=1000.0,
                target_white_point=[0.3127, 0.3290],
                target_gamut="bt2020",
                llm=llm,
                repass_count=0,
            )

        assert result is not None
        assert result.action == "repatch"
        assert "White 10%" in result.patches
        assert "Cyan 75%" in result.patches

    def test_handles_invalid_action_falls_back_to_accept(self):
        """Invalid action value falls back to 'accept'."""
        llm = MagicMock()
        llm.endpoint = "http://localhost:4000"
        llm.model = "test-model"
        llm.api_key = ""
        llm.timeout = 30.0

        mock_response = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "action": "unknown_action",
                        "patches": [],
                        "reason": "test",
                        "confidence": 0.5,
                        "repass_count": 0,
                    })
                }
            }]
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None

            result = query_pass_decision(
                measurements=[{"label": "White 50%", "delta_e": 1.0, "stimulus_pct": 50}],
                phase="pre_grayscale",
                signal_range="full",
                code_scale="8bit",
                target_gamma=2.2,
                target_peak_nits=120.0,
                target_white_point=[0.3127, 0.3290],
                target_gamut="bt709",
                llm=llm,
            )

        assert result is not None
        assert result.action == "accept"  # falls back

    def test_non_numeric_delta_e_returns_none_not_raise(self):
        """Non-numeric delta_e values must degrade to None, never raise."""
        llm = MagicMock()
        llm.endpoint = "http://localhost:4000"
        llm.model = "test-model"
        llm.api_key = ""
        llm.timeout = 30.0

        result = query_pass_decision(
            measurements=[{"label": "White 50%", "delta_e": "bad"}],
            phase="pre_grayscale",
            signal_range="full",
            code_scale="8bit",
            target_gamma=2.2,
            target_peak_nits=120.0,
            target_white_point=[0.3127, 0.3290],
            target_gamut="bt709",
            llm=llm,
        )

        assert result is None

    def test_non_numeric_effective_gamma_and_cct_do_not_raise(self):
        """Non-numeric effective_gamma/cct on an otherwise-valid measurement must not raise."""
        llm = MagicMock()
        llm.endpoint = "http://localhost:4000"
        llm.model = "test-model"
        llm.api_key = ""
        llm.timeout = 30.0

        mock_response = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "action": "accept",
                        "patches": [],
                        "reason": "test",
                        "confidence": 0.9,
                        "repass_count": 0,
                        "ceiling_reason": None,
                    })
                }
            }]
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None

            result = query_pass_decision(
                measurements=[{
                    "label": "White 50%",
                    "delta_e": 1.2,
                    "stimulus_pct": 50,
                    "effective_gamma": "bad",
                    "cct": "bad",
                }],
                phase="pre_grayscale",
                signal_range="full",
                code_scale="8bit",
                target_gamma=2.2,
                target_peak_nits=120.0,
                target_white_point=[0.3127, 0.3290],
                target_gamut="bt709",
                llm=llm,
            )

        assert result is not None
        assert result.action == "accept"


class TestTVProfileThresholds:
    """Quality gate thresholds in TV profiles."""

    def test_u8g_has_thresholds(self):
        """U8G profile includes quality_gate_thresholds."""
        u8g = TV_PROFILES["u8g"]
        assert hasattr(u8g, "quality_gate_thresholds")
        t = u8g.quality_gate_thresholds
        assert "grayscale" in t
        assert "white_balance" in t
        assert "color_tuner" in t
        assert "gamma" in t

    def test_u8g_grayscale_thresholds(self):
        """U8G grayscale thresholds match spec."""
        t = TV_PROFILES["u8g"].quality_gate_thresholds
        assert t["grayscale"]["avg_de"] == 2.0
        assert t["grayscale"]["max_de"] == 3.0

    def test_u8g_color_tuner_thresholds(self):
        """U8G color tuner thresholds match spec."""
        t = TV_PROFILES["u8g"].quality_gate_thresholds
        assert t["color_tuner"]["avg_de"] == 3.0
        assert t["color_tuner"]["max_de"] == 4.0

    def test_other_profiles_have_empty_thresholds(self):
        """Profiles without explicit thresholds have empty dict."""
        for key, profile in TV_PROFILES.items():
            if key == "u8g":
                assert profile.quality_gate_thresholds != {}
            else:
                assert profile.quality_gate_thresholds == {}


class TestSessionRepashCount:
    """repass_count field in session serialization."""

    def test_serialize_session_includes_repass_count(self):
        """serialize_session includes repass_count."""
        from calibrator.session import serialize_session

        session = {
            "id": "test123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "report",
            "mode": "HDR10",
            "gamma_workflow": "quick",
            "signal_range": "full",
            "code_scale": "8bit",
            "lightspace_tier": "free",
            "pattern_generator": "dogegen",
            "grayscale_ramp_steps": 11,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 1000.0,
            "created_at": "2025-01-01T00:00:00+00:00",
            "last_accessed_at": "2025-01-01T00:00:00+00:00",
            "zro_imports": [],
            "llm_config": {"endpoint": "", "model": "", "temperature": 0.2, "timeout": 30.0},
            "repass_count": 2,
        }

        result = serialize_session(session)
        assert result["repass_count"] == 2

    def test_serialize_session_defaults_to_zero(self):
        """serialize_session defaults repass_count to 0."""
        from calibrator.session import serialize_session

        session = {
            "id": "test123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "report",
            "mode": "HDR10",
            "gamma_workflow": "quick",
            "signal_range": "full",
            "code_scale": "8bit",
            "lightspace_tier": "free",
            "pattern_generator": "dogegen",
            "grayscale_ramp_steps": 11,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 1000.0,
            "created_at": "2025-01-01T00:00:00+00:00",
            "last_accessed_at": "2025-01-01T00:00:00+00:00",
            "zro_imports": [],
            "llm_config": {"endpoint": "", "model": "", "temperature": 0.2, "timeout": 30.0},
        }

        result = serialize_session(session)
        assert result["repass_count"] == 0

    def test_deserialize_session_includes_repass_count(self):
        """deserialize_session includes repass_count."""
        from calibrator.session import deserialize_session

        data = {
            "id": "test123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "report",
            "mode": "HDR10",
            "llm_config": {"endpoint": "", "model": "", "temperature": 0.2, "timeout": 30.0},
            "repass_count": 1,
        }

        result = deserialize_session(data)
        assert result["repass_count"] == 1

    def test_deserialize_session_defaults_to_zero(self):
        """deserialize_session defaults repass_count to 0."""
        from calibrator.session import deserialize_session

        data = {
            "id": "test123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "report",
            "mode": "HDR10",
            "llm_config": {"endpoint": "", "model": "", "temperature": 0.2, "timeout": 30.0},
        }

        result = deserialize_session(data)
        assert result["repass_count"] == 0


class TestReportPayloadRepashCount:
    """repass_count in report payload."""

    def test_report_payload_includes_repass_count(self):
        """report_payload includes repass_count from session."""
        from calibrator.reports import report_payload

        session = {
            "id": "test",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "mode": "HDR10",
            "signal_range": "full",
            "code_scale": "8bit",
            "target": MagicMock(gamut="bt2020", eotf="pq", peak_luminance_nits=1000.0, white_point_xy=(0.3127, 0.3290)),
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "cms_measurements": [],
            "gamma_measurements": [],
            "peak_luminance": 1000.0,
            "created_at": "2025-01-01T00:00:00+00:00",
            "repass_count": 2,
        }

        result = report_payload(session)
        assert result["repass_count"] == 2

    def test_report_payload_defaults_repass_count(self):
        """report_payload defaults repass_count to 0."""
        from calibrator.reports import report_payload

        session = {
            "id": "test",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "mode": "HDR10",
            "signal_range": "full",
            "code_scale": "8bit",
            "target": MagicMock(gamut="bt2020", eotf="pq", peak_luminance_nits=1000.0, white_point_xy=(0.3127, 0.3290)),
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "cms_measurements": [],
            "gamma_measurements": [],
            "peak_luminance": 1000.0,
            "created_at": "2025-01-01T00:00:00+00:00",
        }

        result = report_payload(session)
        assert result["repass_count"] == 0


class TestSessionViewRepashCount:
    """repass_count in session view."""

    def test_session_view_includes_repass_count(self):
        """session_view includes repass_count."""
        from calibrator.session import session_view

        session = {
            "id": "test",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "report",
            "mode": "HDR10",
            "gamma_workflow": "quick",
            "signal_range": "full",
            "code_scale": "8bit",
            "lightspace_tier": "free",
            "pattern_generator": "dogegen",
            "grayscale_ramp_steps": 11,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 1000.0,
            "created_at": "2025-01-01T00:00:00+00:00",
            "last_accessed_at": "2025-01-01T00:00:00+00:00",
            "zro_imports": [],
            "llm_config": {"endpoint": "", "model": ""},
            "repass_count": 3,
            "target": MagicMock(gamut="bt2020", eotf="pq", peak_luminance_nits=1000.0, white_point_xy=(0.3127, 0.3290), gamma=0.45),
        }

        result = session_view(session)
        assert result["repass_count"] == 3


class TestPassDecisionMeasurementParsing:
    """query_pass_decision correctly parses measurement data."""

    def test_separates_grayscale_from_colors(self):
        """Measurements are correctly categorized as grayscale vs color."""
        llm = MagicMock()
        llm.endpoint = "http://localhost:4000"
        llm.model = "test-model"
        llm.api_key = ""
        llm.timeout = 30.0

        mock_response = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "action": "accept",
                        "patches": [],
                        "reason": "test",
                        "confidence": 0.9,
                        "repass_count": 0,
                    })
                }
            }]
        }

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None

            result = query_pass_decision(
                measurements=[
                    {"label": "White 50%", "delta_e": 1.0, "stimulus_pct": 50, "effective_gamma": 2.18, "x": 0.31, "y": 0.33, "Y": 50.0, "cct": 6500},
                    {"label": "Red 100%", "delta_e": 2.5, "stimulus_pct": 100, "x": 0.68, "y": 0.32, "Y": 70.0},
                    {"label": "White 80%", "delta_e": 1.5, "stimulus_pct": 80, "effective_gamma": 2.22, "x": 0.31, "y": 0.33, "Y": 90.0, "cct": 6510},
                    {"label": "Blue 100%", "delta_e": 3.0, "stimulus_pct": 100, "x": 0.15, "y": 0.06, "Y": 10.0},
                ],
                phase="post_grayscale",
                signal_range="full",
                code_scale="8bit",
                target_gamma=2.2,
                target_peak_nits=120.0,
                target_white_point=[0.3127, 0.3290],
                target_gamut="bt709",
                llm=llm,
            )

        assert result is not None
        assert result.action == "accept"


class TestRepashTargetStep:
    """_repass_target_step helper function."""

    def test_stays_in_measurement_step(self):
        for step in ("pre_grayscale", "white_balance", "gamma", "color_tuner", "post_grayscale"):
            assert _repass_target_step(step) == step

    def test_jumps_from_report_to_post_grayscale(self):
        assert _repass_target_step("report") == "post_grayscale"

    def test_jumps_from_suggested_patches_to_post_grayscale(self):
        assert _repass_target_step("suggested_patches") == "post_grayscale"

    def test_jumps_from_prepare_to_post_grayscale(self):
        assert _repass_target_step("prepare") == "post_grayscale"


class TestSessionStoreRepash:
    """SessionStore.repass() method."""

    def _make_store(self, session_data: Dict[str, Any]) -> SessionStore:
        from datetime import datetime, timezone
        now = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        session_data["created_at"] = now
        session_data["last_accessed_at"] = now
        store = SessionStore(
            session_dir_getter=lambda: MagicMock(),
            ttl_getter=lambda: timedelta(days=7),
            watched_session_id_getter=lambda: None,
        )
        store.sessions[session_data["id"]] = session_data
        return store

    def _base_session(self, step: str = "post_grayscale") -> Dict[str, Any]:
        return {
            "id": "test1234",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": step,
            "mode": "HDR10",
            "gamma_workflow": "quick",
            "signal_range": "full",
            "code_scale": "8bit",
            "lightspace_tier": "free",
            "pattern_generator": "dogegen",
            "grayscale_ramp_steps": 11,
            "sdr_peak_nits": None,
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "lum_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "peak_luminance": 1000.0,
            "created_at": "2025-01-01T00:00:00+00:00",
            "last_accessed_at": "2025-01-01T00:00:00+00:00",
            "zro_imports": [],
            "llm_config": {"endpoint": "", "model": "", "temperature": 0.2, "timeout": 30.0},
            "repass_count": 0,
        }

    def test_repatch_increments_count(self):
        store = self._make_store(self._base_session())
        session = store.repass("test1234", "repatch", patches=["Red"], reason="high dE")
        assert session["repass_count"] == 1

    def test_repatch_jumps_to_measurement_step(self):
        store = self._make_store(self._base_session("post_grayscale"))
        session = store.repass("test1234", "repatch", reason="re-measure")
        assert session["step"] == "post_grayscale"

    def test_repatch_preserves_current_step(self):
        store = self._make_store(self._base_session("white_balance"))
        session = store.repass("test1234", "repatch", reason="re-measure")
        assert session["step"] == "white_balance"

    def test_repatch_records_decision_metadata(self):
        store = self._make_store(self._base_session())
        session = store.repass("test1234", "repatch", patches=["Red", "Green"], reason="color error")
        assert session["repass_decision"]["action"] == "repatch"
        assert session["repass_decision"]["reason"] == "color error"
        assert session["repass_decision"]["patches"] == ["Red", "Green"]
        assert "timestamp" in session["repass_decision"]

    def test_repatch_hits_max_and_flags_ceiling(self):
        session = self._base_session()
        session["repass_count"] = 3
        store = self._make_store(session)
        result = store.repass("test1234", "repatch", reason="still bad")
        assert result["repass_decision"]["action"] == "ceiling"
        assert "Max repass limit" in result["repass_decision"]["reason"]

    def test_ceiling_action_sets_ceiling_flag(self):
        store = self._make_store(self._base_session())
        session = store.repass(
            "test1234",
            "ceiling",
            reason="hardware limit",
            ceiling_reason="Blue primary dE 7.2",
        )
        assert session["repass_decision"]["action"] == "ceiling"
        assert session["repass_decision"]["ceiling_reason"] == "Blue primary dE 7.2"

    def test_accept_action_does_not_increment(self):
        store = self._make_store(self._base_session())
        session = store.repass("test1234", "accept", reason="within tolerance")
        assert session["repass_count"] == 0

    def test_repass_from_report_jumps_to_post_grayscale(self):
        store = self._make_store(self._base_session("report"))
        session = store.repass("test1234", "repatch", reason="final check")
        assert session["step"] == "post_grayscale"


class TestLabelToRgb:
    """_label_to_rgb helper function (imported from server.py)."""

    def test_grayscale_white_10_percent(self):
        from server import _label_to_rgb
        rgb = _label_to_rgb("White 10%", "full")
        assert len(rgb) == 3
        assert rgb[0] == rgb[1] == rgb[2]

    def test_grayscale_white_80_percent(self):
        from server import _label_to_rgb
        rgb = _label_to_rgb("White 80%", "full")
        assert len(rgb) == 3
        assert rgb[0] == rgb[1] == rgb[2]

    def test_cms_red(self):
        from server import _label_to_rgb
        rgb = _label_to_rgb("Red", "full")
        assert rgb == [255, 0, 0]

    def test_cms_cyan(self):
        from server import _label_to_rgb
        rgb = _label_to_rgb("Cyan", "full")
        assert rgb == [0, 255, 255]

    def test_cms_red_full_10bit(self):
        from server import _label_to_rgb
        rgb = _label_to_rgb("Red", "full", "10bit")
        assert rgb == [1023, 0, 0]

    def test_cms_yellow(self):
        from server import _label_to_rgb
        rgb = _label_to_rgb("Yellow", "full")
        assert rgb == [255, 255, 0]

    def test_rgb_label(self):
        from server import _label_to_rgb
        rgb = _label_to_rgb("RGB(235,16,16)", "limited")
        assert rgb == [235, 16, 16]

    def test_fallback_white(self):
        from server import _label_to_rgb
        rgb = _label_to_rgb("UnknownPatch", "full")
        assert rgb == [235, 235, 235]


def _summary(
    *,
    avg_de=None,
    max_de=None,
    gamma=None,
    color_avg=None,
    color_max=None,
) -> Summary:
    """Build a Summary with the scalar residuals convergence logic reads."""
    return Summary(
        grayscale_avg_de=avg_de,
        grayscale_max_de=max_de,
        grayscale_over_3=0,
        gamma_midtones=gamma,
        pq_err_midtones=None,
        color_75_avg_de=None,
        color_75_max_de=None,
        color_75_chroma_avg=None,
        color_100_avg_de=color_avg,
        color_100_max_de=color_max,
        color_100_chroma_avg=None,
        grayscale_rows=[],
        color_rows=[],
        meta={},
        measured_patch_count=11,
        expected_patch_count=11,
    )


def _llm_mock():
    llm = MagicMock()
    llm.endpoint = "http://localhost:4000"
    llm.model = "test-model"
    llm.api_key = ""
    llm.timeout = 30.0
    llm.provider = "openai"
    return llm


def _cfg_mock():
    cfg = MagicMock()
    cfg.mode = "SDR"
    cfg.eotf = "gamma"
    cfg.target_space = "bt709"
    return cfg


def _patch_urlopen(payload: Dict[str, Any]):
    """Context manager patching urlopen to return a canned chat-completion body."""
    mock_response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    p = patch("urllib.request.urlopen")
    mock_urlopen = p.start()
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_response).encode()
    mock_urlopen.return_value.__enter__ = lambda s: mock_resp
    mock_urlopen.return_value.__exit__ = lambda s, *a: None
    return p, mock_urlopen


class TestAssessConvergence:
    """Deterministic convergence assessment (#337)."""

    def test_converged_within_default_grayscale_thresholds(self):
        a = assess_convergence(_summary(avg_de=1.2, max_de=2.5), "post_grayscale")
        assert a.converged is True
        assert a.stalled is False
        assert a.metric_key == "grayscale"

    def test_not_converged_when_avg_exceeds(self):
        a = assess_convergence(_summary(avg_de=3.0, max_de=2.0), "post_grayscale")
        assert a.converged is False

    def test_not_converged_when_max_exceeds(self):
        a = assess_convergence(_summary(avg_de=1.0, max_de=4.0), "post_grayscale")
        assert a.converged is False

    def test_no_data_is_not_converged(self):
        a = assess_convergence(_summary(), "post_grayscale")
        assert a.converged is False
        assert "No grayscale" in a.detail

    def test_color_phase_uses_color_residuals(self):
        a = assess_convergence(
            _summary(avg_de=9.9, max_de=9.9, color_avg=2.0, color_max=3.5),
            "color_tuner",
        )
        assert a.metric_key == "color_tuner"
        assert a.converged is True

    def test_stalled_when_improvement_below_epsilon(self):
        a = assess_convergence(
            _summary(avg_de=4.0, max_de=5.0),
            "post_grayscale",
            rounds_used=1,
            prev_avg_de=4.0 + _CONVERGENCE_STALL_EPSILON / 2,
        )
        assert a.converged is False
        assert a.stalled is True

    def test_not_stalled_when_improving(self):
        a = assess_convergence(
            _summary(avg_de=3.0, max_de=4.0),
            "post_grayscale",
            rounds_used=1,
            prev_avg_de=5.0,
        )
        assert a.stalled is False

    def test_rounds_remaining_decrements_with_cap(self):
        a = assess_convergence(
            _summary(avg_de=3.0, max_de=4.0),
            "post_grayscale",
            rounds_used=2,
            round_cap=3,
        )
        assert a.rounds_remaining == 1

    def test_gamma_phase_gates_on_gamma_deviation(self):
        # avg/max grayscale within tolerance but gamma off by 0.2 — not converged.
        a = assess_convergence(
            _summary(avg_de=1.0, max_de=2.0, gamma=2.0),
            "gamma",
            target_gamma=2.2,
        )
        assert a.metric_key == "gamma"
        assert a.converged is False

    def test_profile_thresholds_override_defaults(self):
        thresholds = {"grayscale": {"avg_de": 0.5, "max_de": 1.0}}
        a = assess_convergence(
            _summary(avg_de=1.2, max_de=2.5),
            "post_grayscale",
            thresholds=thresholds,
        )
        assert a.converged is False  # tighter profile threshold fails


class TestPredictNextSettings:
    """Convergence-aware next-settings prediction (#337)."""

    def test_returns_none_without_llm_config(self):
        llm = MagicMock()
        llm.endpoint = ""
        llm.model = ""
        result = predict_next_settings(
            _summary(avg_de=1.0, max_de=2.0), _cfg_mock(), "post_grayscale", llm
        )
        assert result is None

    def test_converged_short_circuits_without_network(self):
        llm = _llm_mock()
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = predict_next_settings(
                _summary(avg_de=1.2, max_de=2.5),
                _cfg_mock(),
                "post_grayscale",
                llm,
            )
            mock_urlopen.assert_not_called()
        assert result is not None
        assert result.converged is True
        assert result.next_step == "verify"
        assert result.source == "converged"
        assert result.adjustments == []
        assert result.confidence >= 0.9

    def test_round_cap_reached_returns_ceiling_without_network(self):
        llm = _llm_mock()
        prior = [
            {"round": 1, "residual": {"avg_de": 5.0}, "suggested": []},
            {"round": 2, "residual": {"avg_de": 4.5}, "suggested": []},
            {"round": 3, "residual": {"avg_de": 4.2}, "suggested": []},
        ]
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = predict_next_settings(
                _summary(avg_de=4.0, max_de=5.0),
                _cfg_mock(),
                "post_grayscale",
                llm,
                prior_rounds=prior,
                round_cap=3,
            )
            mock_urlopen.assert_not_called()
        assert result is not None
        assert result.next_step == "ceiling"
        assert result.source == "ceiling"
        assert result.rounds_remaining == 0

    def test_stall_returns_ceiling_without_network(self):
        llm = _llm_mock()
        prior = [{"round": 1, "residual": {"avg_de": 4.1}, "suggested": []}]
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = predict_next_settings(
                _summary(avg_de=4.0, max_de=5.0),
                _cfg_mock(),
                "post_grayscale",
                llm,
                prior_rounds=prior,
                round_cap=3,
            )
            mock_urlopen.assert_not_called()
        assert result is not None
        assert result.next_step == "ceiling"
        assert result.stalled is True

    def test_out_of_tolerance_calls_llm_and_parses(self):
        llm = _llm_mock()
        plan = {
            "adjustments": [
                {
                    "menu": "White Balance",
                    "setting": "R Gain",
                    "from": 0,
                    "to": -3,
                    "scope": "global",
                    "reason": "Reduce red push in highlights.",
                }
            ],
            "next_step": "rerun_grayscale",
            "confidence": 0.8,
        }
        p, mock_urlopen = _patch_urlopen(plan)
        try:
            result = predict_next_settings(
                _summary(avg_de=3.5, max_de=5.0),
                _cfg_mock(),
                "post_grayscale",
                llm,
                prior_rounds=[
                    {
                        "round": 1,
                        "residual": {"avg_de": 5.0, "max_de": 7.0},
                        "suggested": [
                            {"menu": "White Balance", "setting": "R Gain", "to": -1}
                        ],
                    }
                ],
            )
            mock_urlopen.assert_called_once()
        finally:
            p.stop()
        assert result is not None
        assert result.source == "llm"
        assert result.converged is False
        assert result.rounds_used == 1
        assert len(result.adjustments) == 1
        assert result.next_step == "rerun_grayscale"
        assert result.confidence == 0.8

    def test_unparseable_llm_response_returns_none(self):
        llm = _llm_mock()
        mock_response = {"choices": [{"message": {"content": "not json at all"}}]}
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode()
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = lambda s, *a: None
            result = predict_next_settings(
                _summary(avg_de=3.5, max_de=5.0),
                _cfg_mock(),
                "post_grayscale",
                llm,
            )
        assert result is None


class TestNextSettingsPredictionDataclass:
    """NextSettingsPrediction.to_dict contract."""

    def test_to_dict_round_trips_fields(self):
        pred = NextSettingsPrediction(
            adjustments=[{"menu": "m", "setting": "s", "to": 1, "scope": "global"}],
            next_step="verify",
            confidence=0.95,
            converged=True,
            stalled=False,
            rounds_used=2,
            rounds_remaining=1,
            convergence={"converged": True},
            message="ok",
            source="converged",
        )
        d = pred.to_dict()
        assert d["next_step"] == "verify"
        assert d["converged"] is True
        assert d["rounds_used"] == 2
        assert d["convergence"] == {"converged": True}
        assert d["source"] == "converged"


class TestRecordAdjustmentRound:
    """SessionStore.record_adjustment_round (#337)."""

    def _make_store(self, session_data: Dict[str, Any]) -> SessionStore:
        from datetime import datetime, timezone

        now_iso = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        session_data["created_at"] = now_iso
        session_data["last_accessed_at"] = now_iso
        store = SessionStore(
            session_dir_getter=lambda: MagicMock(),
            ttl_getter=lambda: timedelta(days=7),
            watched_session_id_getter=lambda: None,
        )
        store.sessions[session_data["id"]] = session_data
        return store

    def _session(self) -> Dict[str, Any]:
        return {
            "id": "rec12345",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "post_grayscale",
            "mode": "SDR",
            "llm_config": {"endpoint": "", "model": ""},
        }

    def test_first_round_appended_with_index_one(self):
        store = self._make_store(self._session())
        with patch.object(store, "save_session"):
            session = store.record_adjustment_round(
                "rec12345",
                residual={"avg_de": 3.5, "max_de": 5.0},
                suggested=[{"menu": "WB", "setting": "R Gain", "to": -2}],
            )
        rounds = session["llm_adjustment_rounds"]
        assert len(rounds) == 1
        assert rounds[0]["round"] == 1
        assert rounds[0]["residual"]["avg_de"] == 3.5
        assert "timestamp" in rounds[0]

    def test_rounds_accumulate_and_increment(self):
        store = self._make_store(self._session())
        with patch.object(store, "save_session"):
            store.record_adjustment_round("rec12345", {"avg_de": 5.0}, [])
            session = store.record_adjustment_round("rec12345", {"avg_de": 3.0}, [])
        rounds = session["llm_adjustment_rounds"]
        assert [r["round"] for r in rounds] == [1, 2]


class TestReportPayloadRepashReason:
    """repass_reason field in report payload."""

    def test_report_payload_includes_repass_reason(self):
        from calibrator.reports import report_payload

        session = {
            "id": "test",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "mode": "HDR10",
            "signal_range": "full",
            "code_scale": "8bit",
            "target": MagicMock(gamut="bt2020", eotf="pq", peak_luminance_nits=1000.0, white_point_xy=(0.3127, 0.3290)),
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "cms_measurements": [],
            "gamma_measurements": [],
            "peak_luminance": 1000.0,
            "created_at": "2025-01-01T00:00:00+00:00",
            "repass_count": 2,
            "repass_decision": {"action": "repatch", "reason": "Grayscale avg dE 2.8"},
        }

        result = report_payload(session)
        assert result["repass_count"] == 2
        assert result["repass_reason"] == "Grayscale avg dE 2.8"
        assert result["repass_action"] == "repatch"

    def test_report_payload_defaults_repass_fields(self):
        from calibrator.reports import report_payload

        session = {
            "id": "test",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "mode": "HDR10",
            "signal_range": "full",
            "code_scale": "8bit",
            "target": MagicMock(gamut="bt2020", eotf="pq", peak_luminance_nits=1000.0, white_point_xy=(0.3127, 0.3290)),
            "pre_measurements": [],
            "post_measurements": [],
            "wb_measurements": [],
            "cms_measurements": [],
            "gamma_measurements": [],
            "peak_luminance": 1000.0,
            "created_at": "2025-01-01T00:00:00+00:00",
        }

        result = report_payload(session)
        assert result["repass_reason"] == ""
        assert result["repass_action"] == ""
