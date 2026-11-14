"""Manual upload and watch-folder imports must land identical session buckets.

#707 collapsed the duplicated step routing into one function; these tests pin
the behaviour end to end by feeding the same CSV sequence through
``SessionStore.import_zro_bytes`` (via the API) and ``_ZROHandler._do_import_file``
and comparing every measurement bucket, including multi-pass grayscale logs and
partial-overlap reimports.
"""

import io
import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import calibrator.file_watcher as fw
from calibrator.session import deserialize_measurement
from server import _sessions, app

BUCKETS = (
    "pre_measurements",
    "post_measurements",
    "lum_measurements",
    "wb_measurements",
    "gamma_measurements",
    "cms_measurements",
)
RAMP_CODES = (16, 26, 51, 77, 102, 128, 153, 179, 204, 230, 235)
COLOURS = ((235, 16, 16), (16, 235, 16), (16, 16, 235))
BASE = datetime(2026, 3, 15, 10, 0, 0)


def _row(ts, rgb, y):
    return f"{ts:%d/%m/%Y %H:%M:%S}\t{rgb[0]}\t{rgb[1]}\t{rgb[2]}\t{y}\t0.313\t0.329\t 360ms\n"


def _ramp(minute):
    start = BASE + timedelta(minutes=minute)
    return "".join(
        _row(start + timedelta(seconds=3 * i), (c, c, c), round(c * 0.4, 2))
        for i, c in enumerate(RAMP_CODES)
    )


def _colours(minute):
    start = BASE + timedelta(minutes=minute)
    return "".join(
        _row(start + timedelta(seconds=3 * i), rgb, 20.0 + i)
        for i, rgb in enumerate(COLOURS)
    )


HEADER = "Date and time\tR\tG\tB\tY\tx\ty\tmsec\n"
# Ramps are 10 minutes apart so each is its own ZRO session group.
CSV_STEPS = {
    "single_ramp": [HEADER + _ramp(0)],
    "three_pass_log": [HEADER + _ramp(0) + _ramp(10) + _ramp(20)],
    "grown_log": [
        HEADER + _ramp(0) + _ramp(10),
        HEADER + _ramp(0) + _ramp(10) + _ramp(20),
    ],
    "same_file_twice": [HEADER + _ramp(0) + _ramp(10)] * 2,
    "ramp_then_colours_overlap": [
        HEADER + _ramp(0) + _colours(10),
        HEADER + _ramp(0) + _colours(10) + _colours(20),
    ],
}


@pytest.fixture(autouse=True)
def clean():
    _sessions.clear()
    fw.stop_watching()
    yield
    fw.stop_watching()
    _sessions.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _new_session(client, step):
    resp = client.post("/api/session", json={"tv_key": "u8g", "simulate": True})
    sid = resp.json()["id"]
    client.post(f"/api/session/{sid}/mode", json={"mode": "SDR"})
    client.post(f"/api/session/{sid}/prepared")
    _sessions[sid]["step"] = step
    return sid


def _signatures(sid):
    return {
        b: [fw._measurement_signature(m) for m in _sessions[sid].get(b, [])]
        for b in BUCKETS
    }


_RAMP_SCENARIOS = ("single_ramp", "three_pass_log", "grown_log", "same_file_twice")
CASES = [(sc, st) for sc in _RAMP_SCENARIOS for st in ("pre_grayscale", "post_grayscale", "gamma")]
CASES += [("ramp_then_colours_overlap", st) for st in ("pre_grayscale", "color_tuner")]


@pytest.mark.parametrize("scenario,step", CASES)
def test_manual_and_watcher_imports_agree(client, tmp_path, step, scenario):
    manual = _new_session(client, step)
    watched = _new_session(client, step)
    handler = fw._ZROHandler(
        lambda: _sessions[watched],
        lambda: None,
        measurement_deserializer=deserialize_measurement,
    )

    populated = False
    for i, text in enumerate(CSV_STEPS[scenario]):
        client.post(
            f"/api/session/{manual}/import/zro",
            files={"file": ("log.csv", io.BytesIO(text.encode()), "text/csv")},
        )
        path = tmp_path / "log.csv"
        path.write_text(text)
        # Distinct mtime per write so the watcher's duplicate suppression
        # doesn't mask a reimport.
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
        handler._do_import_file(str(path))

        assert _signatures(watched) == _signatures(manual), f"{scenario}/{step} import {i}"
        populated = populated or any(_signatures(manual).values())

    assert populated, f"{scenario}/{step} routed nothing; the comparison proved nothing"
