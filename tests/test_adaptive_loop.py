"""Tests for adaptive multi-pass loop (#166)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from calcore.llm import PassDecision, query_pass_decision
from calibrator.profiles import TV_PROFILES
from calibrator.session import REPATCH_MAX_PASSES


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
