"""Tests for calibrator/session.py deserialization and thread-safety."""

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from calibrator.session import (
    SessionStore,
    deserialize_session,
    deserialize_measurement,
)


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
        with pytest.raises(ValueError, match="missing required field"):
            deserialize_session(data)

    def test_corrupted_json_missing_tv_key(self):
        data = {
            "id": "test-123",
            "tv_name": "Hisense U8G",
            "step": "baseline",
        }
        with pytest.raises(ValueError, match="missing required field"):
            deserialize_session(data)

    def test_corrupted_json_missing_step(self):
        data = {
            "id": "test-123",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
        }
        with pytest.raises(ValueError, match="missing required field"):
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
        with pytest.raises(ValueError, match="invalid mode"):
            deserialize_session(data)

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


class TestSessionStoreThreadSafety:
    """Verify that SessionStore serializes concurrent access to self.sessions."""

    @pytest.fixture
    def store(self, tmp_path):
        return SessionStore(
            session_dir_getter=lambda: tmp_path,
            ttl_getter=lambda: timedelta(days=7),
            watched_session_id_getter=lambda: None,
        )

    def test_concurrent_create_and_get(self, store):
        """create_session and get should not corrupt state under concurrent access."""
        errors = []

        def create_sessions(n):
            for _ in range(n):
                try:
                    s = store.create_session("u8g")
                    store.get(s["id"])
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=create_sessions, args=(50,)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent access: {errors}"
        # Every session created should be retrievable
        assert len(store.sessions) == 400

    def test_concurrent_get_and_evict(self, store):
        """get() calling evict_expired_sessions() must not race with manual eviction."""
        # Seed with sessions that will expire
        store.create_session("u8g")
        store.create_session("u8g")

        barrier = threading.Barrier(2)
        errors = []

        def getter():
            try:
                barrier.wait()
                for _ in range(100):
                    for sid in list(store.sessions.keys()):
                        try:
                            store.get(sid)
                        except Exception:
                            pass  # 404 is fine if evicted
            except Exception as e:
                errors.append(e)

        def evictor():
            try:
                barrier.wait()
                for _ in range(100):
                    store.evict_expired_sessions()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=getter)
        t2 = threading.Thread(target=evictor)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors during concurrent get/evict: {errors}"

    def test_concurrent_delete_and_get(self, store):
        """delete() must not race with get() causing dict-modify-during-iteration."""
        for _ in range(50):
            store.create_session("u8g")

        sids = list(store.sessions.keys())
        errors = []

        def deleter():
            try:
                for sid in sids[:25]:
                    store.delete(sid)
            except Exception as e:
                errors.append(e)

        def getter():
            try:
                for sid in sids[25:]:
                    try:
                        store.get(sid)
                    except Exception:
                        pass
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=deleter)
        t2 = threading.Thread(target=getter)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Errors during concurrent delete/get: {errors}"
        assert len(store.sessions) == 25

    def test_rlock_is_reentrant(self, store):
        """RLock must allow nested acquisition (e.g. get -> evict_expired_sessions)."""
        store.create_session("u8g")
        # get() calls evict_expired_sessions() internally — should not deadlock
        session = store.get(list(store.sessions.keys())[0])
        assert session is not None
