"""Tests for calibrator/utils.py helpers."""

import pytest
from calibrator.utils import get_all_measurements


class TestGetAllMeasurements:
    def test_empty_session(self):
        result = get_all_measurements({})
        assert result == []

    def test_all_buckets_empty(self):
        session = {
            "pre_measurements": [],
            "wb_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "post_measurements": [],
        }
        result = get_all_measurements(session)
        assert result == []

    def test_single_bucket_with_data(self):
        session = {
            "pre_measurements": [{"label": "R100"}],
            "wb_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "post_measurements": [],
        }
        result = get_all_measurements(session)
        assert len(result) == 1
        assert result[0]["label"] == "R100"

    def test_all_buckets_with_data(self):
        session = {
            "pre_measurements": [{"label": "p1"}, {"label": "p2"}],
            "wb_measurements": [{"label": "w1"}],
            "gamma_measurements": [{"label": "g1"}, {"label": "g2"}, {"label": "g3"}],
            "cms_measurements": [{"label": "c1"}],
            "post_measurements": [{"label": "s1"}],
        }
        result = get_all_measurements(session)
        assert len(result) == 8
        labels = [m["label"] for m in result]
        assert labels == ["p1", "p2", "w1", "g1", "g2", "g3", "c1", "s1"]

    def test_missing_keys_returns_empty(self):
        session = {"pre_measurements": [{"label": "x"}]}
        result = get_all_measurements(session)
        assert len(result) == 1
        assert result[0]["label"] == "x"

    def test_none_values_in_buckets(self):
        session = {
            "pre_measurements": None,
            "wb_measurements": [],
            "gamma_measurements": [],
            "cms_measurements": [],
            "post_measurements": [],
        }
        result = get_all_measurements(session)
        assert result == []
