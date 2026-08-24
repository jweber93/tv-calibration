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
    serialize_session,
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


class TestSaveSessionAtomicWrite:
    """#642: save_session must write atomically (tmp + replace), never in place."""

    @pytest.fixture
    def store(self, tmp_path):
        return SessionStore(
            session_dir_getter=lambda: tmp_path,
            ttl_getter=lambda: timedelta(days=7),
            watched_session_id_getter=lambda: None,
        )

    def test_no_tmp_file_left_behind_on_success(self, store, tmp_path):
        result = store.create_session("u8g")
        sid = result["id"]
        store.save_session(sid)

        path = tmp_path / f"{sid}.json"
        tmp = tmp_path / f"{sid}.json.tmp"
        assert path.exists()
        assert not tmp.exists()

    def test_failed_write_leaves_prior_version_intact(self, store, tmp_path, monkeypatch):
        """A failure while writing the .tmp file must not touch the real file —
        os.replace only swaps in a fully-written temp file, so a save_session
        that dies mid-write leaves the previously-persisted version readable
        rather than a truncated/corrupt file."""
        result = store.create_session("u8g")
        sid = result["id"]
        store.save_session(sid)
        path = tmp_path / f"{sid}.json"
        original_contents = path.read_text()

        real_write_text = Path.write_text

        def _boom(self, *args, **kwargs):
            if self.name.endswith(".tmp"):
                raise OSError("disk full")
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", _boom)
        store.sessions[sid]["step"] = "white_balance"
        store.save_session(sid)  # logs and swallows the OSError internally

        # The real file must be untouched by the aborted write.
        assert path.read_text() == original_contents


class TestRepassCeilingBehavior:
    """Test that repass ceiling advances session to report step."""

    @pytest.fixture
    def store(self, tmp_path):
        return SessionStore(
            session_dir_getter=lambda: tmp_path,
            ttl_getter=lambda: timedelta(days=7),
            watched_session_id_getter=lambda: None,
        )

    def test_repatch_ceiling_advances_to_report(self, store):
        """When repatch max passes exceeded, session["step"] should be "report"."""
        result = store.create_session("u8g")
        sid = result["id"]
        session = store.get(sid)
        
        # Set up session in a measurement step (e.g., color_tuner)
        session["step"] = "color_tuner"
        session["mode"] = "SDR"
        session["target"] = MagicMock()
        store.save_session(sid)
        
        # Simulate exceeding repatch limit by calling repass with repatch action
        # multiple times (REPATCH_MAX_PASSES = 3)
        for _ in range(4):
            session = store.repass(sid, "repatch", reason="test")
        
        # After exceeding limit, session should be at report step
        assert session["step"] == "report", "Repatch ceiling should advance to report step"
        assert session["repass_decision"]["action"] == "ceiling"

    def test_ceiling_action_sets_decision_without_repatch_count(self, store):
        """Explicit ceiling action should set repass_decision without incrementing count."""
        result = store.create_session("u8g")
        sid = result["id"]
        session = store.get(sid)
        
        session["step"] = "color_tuner"
        session["mode"] = "SDR"
        session["target"] = MagicMock()
        store.save_session(sid)
        
        # Direct ceiling action
        session = store.repass(sid, "ceiling", reason="hardware limit")
        
        # Should set decision but NOT advance step (ceiling doesn't auto-advance)
        assert session["repass_decision"]["action"] == "ceiling"
        assert session["step"] == "color_tuner", "Direct ceiling should not auto-advance step"


class TestSerializeDeserializeRoundTrip:
    """Serialize → deserialize round-trip preserves all keys."""

    def _minimal_session(self) -> dict:
        return {
            "id": "sid-001",
            "tv_key": "u8g",
            "tv_name": "Hisense U8G",
            "step": "report",
            "mode": "SDR",
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
            "peak_luminance": 500.0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_accessed_at": "2026-01-01T00:00:00+00:00",
            "zro_imports": [],
            "llm_config": {"endpoint": "", "model": "", "temperature": 0.2, "timeout": 30.0},
        }

    def test_round_trip_preserves_tv_settings(self):
        session = self._minimal_session()
        session["tv_settings"] = {
            "two_point_wb": {"offset_r": 1, "offset_g": 2, "offset_b": 3},
            "multipoint_wb": {},
            "cms_sliders": {"red_hue": 0},
        }
        serialized = serialize_session(session)
        deserialized = deserialize_session(serialized)
        assert deserialized["tv_settings"] == session["tv_settings"]

    def test_round_trip_preserves_history_recorded(self):
        session = self._minimal_session()
        session["_history_recorded"] = True
        serialized = serialize_session(session)
        deserialized = deserialize_session(serialized)
        assert deserialized["_history_recorded"] is True

    def test_round_trip_preserves_repass_decision(self):
        session = self._minimal_session()
        session["repass_decision"] = {
            "action": "ceiling",
            "reason": "Exceeded max repasses",
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        serialized = serialize_session(session)
        deserialized = deserialize_session(serialized)
        assert deserialized["repass_decision"] == session["repass_decision"]

    def test_round_trip_defaults_when_missing(self):
        session = self._minimal_session()
        serialized = serialize_session(session)
        deserialized = deserialize_session(serialized)
        assert deserialized["tv_settings"] == {}
        assert deserialized["_history_recorded"] is False
        assert deserialized["repass_decision"] == {}
