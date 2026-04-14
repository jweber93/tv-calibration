"""Tests for calibrator/session.py deserialization functions."""

import pytest
from calibrator.session import deserialize_session, deserialize_measurement


class TestDeserializeMeasurement:
    def test_valid_measurement(self):
        data = {
            "label": "White (100%)",
            "stimulus_rgb": [1.0, 1.0, 1.0],
            "X": 100.0,
            "Y": 100.0,
            "Z": 100.0,
            "x": 0.3127,
            "y": 0.3290,
        }
        m = deserialize_measurement(data)
        assert m.label == "White (100%)"
        assert m.stimulus_rgb == (1.0, 1.0, 1.0)
        assert m.X == 100.0
        assert m.Y == 100.0
        assert m.Z == 100.0

    def test_missing_all_fields_returns_defaults(self):
        data = {}
        m = deserialize_measurement(data)
        assert m.label == ""
        assert m.X == 0.0
        assert m.Y == 0.0
        assert m.Z == 0.0
        assert m.stimulus_rgb == (255, 255, 255)

    def test_partial_fields(self):
        data = {"label": "Red", "stimulus_rgb": [1.0, 0.0, 0.0]}
        m = deserialize_measurement(data)
        assert m.label == "Red"
        assert m.stimulus_rgb == (1.0, 0.0, 0.0)
        assert m.X == 0.0
        assert m.Y == 0.0
        assert m.Z == 0.0


class TestDeserializeSession:
    def test_valid_session(self):
        data = {
            "id": "test-123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "baseline",
            "mode": "SDR",
            "sdr_peak_nits": 1000.0,
            "pre_measurements": [],
            "wb_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "post_measurements": [],
            "lum_measurements": [],
        }
        sess = deserialize_session(data)
        assert sess["id"] == "test-123"
        assert sess["tv_key"] == "u8g"
        assert sess["mode"] == "SDR"
        assert sess["sdr_peak_nits"] == 1000.0

    def test_corrupted_json_missing_id(self):
        data = {
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "baseline",
        }
        with pytest.raises(KeyError):
            deserialize_session(data)

    def test_corrupted_json_missing_tv_key(self):
        data = {
            "id": "test-123",
            "tv_name": "Hisense U8G",
            "step": "baseline",
        }
        with pytest.raises(KeyError):
            deserialize_session(data)

    def test_corrupted_json_missing_step(self):
        data = {
            "id": "test-123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
        }
        with pytest.raises(KeyError):
            deserialize_session(data)

    def test_invalid_mode_type(self):
        data = {
            "id": "test-123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "baseline",
            "mode": 12345,
            "sdr_peak_nits": 1000.0,
            "pre_measurements": [],
            "wb_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "post_measurements": [],
            "lum_measurements": [],
        }
        sess = deserialize_session(data)
        assert sess["mode"] == 12345

    def test_invalid_measurements_not_list(self):
        data = {
            "id": "test-123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "baseline",
            "mode": "SDR",
            "sdr_peak_nits": 1000.0,
            "pre_measurements": "not a list",
            "wb_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "post_measurements": [],
            "lum_measurements": [],
        }
        with pytest.raises(ValueError):
            deserialize_session(data)

    def test_corrupted_measurement_in_list_uses_defaults(self):
        data = {
            "id": "test-123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "baseline",
            "mode": "SDR",
            "sdr_peak_nits": 1000.0,
            "pre_measurements": [{"label": "invalid", "stimulus_rgb": [1.0, 1.0, 1.0]}],
            "wb_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "post_measurements": [],
            "lum_measurements": [],
        }
        sess = deserialize_session(data)
        assert len(sess["pre_measurements"]) == 1
        m = sess["pre_measurements"][0]
        assert m.label == "invalid"
        assert m.X == 0.0
        assert m.Y == 0.0

    def test_empty_list_measurements(self):
        data = {
            "id": "test-123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "baseline",
            "mode": "SDR",
            "pre_measurements": [],
            "wb_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "post_measurements": [],
            "lum_measurements": [],
        }
        sess = deserialize_session(data)
        assert sess["pre_measurements"] == []
        assert sess["wb_measurements"] == []

    def test_missing_measurements_keys(self):
        data = {
            "id": "test-123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "baseline",
        }
        sess = deserialize_session(data)
        assert sess["pre_measurements"] == []
        assert sess["wb_measurements"] == []
