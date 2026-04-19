"""End-to-end workflow coverage for the main calibration journey."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import calibrator.file_watcher as fw
from server import app, _sessions


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    _sessions.clear()
    fw.stop_watching()
    monkeypatch.setattr(fw, "DEBOUNCE_SECONDS", 0.05)
    monkeypatch.setattr(fw, "FILE_READ_RETRY_DELAY", 0.01)
    if hasattr(fw.Observer, "POLL_INTERVAL_SECONDS"):
        monkeypatch.setattr(fw.Observer, "POLL_INTERVAL_SECONDS", 0.02)
    yield
    fw.stop_watching()
    _sessions.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _make_zro_csv(rows):
    header = "Date and time\tR\tG\tB\tY\tx\ty\tmsec"
    lines = [header]
    for i, (r, g, b, Y, x, y) in enumerate(rows):
        ts = f"03/15/2026 10:00:{i:02d}"
        lines.append(f"{ts}\t{r}\t{g}\t{b}\t{Y:.2f}\t{x:.4f}\t{y:.4f}\t50")
    return "\n".join(lines)


def _grayscale_rows(peak_nits):
    rows = []
    for pct in range(0, 101, 10):
        v = round(pct / 100 * 235)
        Y = 0.02 if pct == 0 else peak_nits * pct / 100
        rows.append((v, v, v, Y, 0.3127, 0.3290))
    return rows


def _upload_csv(client, sid, rows, name="test.csv"):
    csv_str = _make_zro_csv(rows)
    return client.post(
        f"/api/session/{sid}/import/zro",
        files={"file": (name, csv_str.encode(), "text/csv")},
    )


def _wait_for(condition, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_full_workflow_from_watch_import_to_report(client, tmp_path):
    create = client.post("/api/session", json={"tv_key": "u8g"})
    assert create.status_code == 200
    session = create.json()
    sid = session["id"]

    mode = client.post(f"/api/session/{sid}/mode", json={"mode": "SDR", "sdr_peak_nits": 140})
    assert mode.status_code == 200
    assert mode.json()["target"]["peak_nits"] == 140

    prepared = client.post(f"/api/session/{sid}/prepared")
    assert prepared.status_code == 200
    assert prepared.json()["step"] == "pre_grayscale"

    import server as server_module
    server_module._WATCH_ROOT = tmp_path.resolve()
    watch = client.post("/api/watch/config", json={"path": str(tmp_path), "sid": sid})
    assert watch.status_code == 200
    assert watch.json()["watching"] is True

    (tmp_path / "pre_gray.csv").write_text(_make_zro_csv(_grayscale_rows(140)))

    assert _wait_for(
        lambda: len(_sessions[sid]["pre_measurements"]) == 11
    ), "watch import should populate the full pre-cal grayscale ramp"

    session = client.get(f"/api/session/{sid}").json()
    assert session["step"] == "pre_grayscale"
    assert len(_sessions[sid]["pre_measurements"]) == 11
    to_luminance = client.post(f"/api/session/{sid}/next")
    assert to_luminance.status_code == 200
    assert to_luminance.json()["step"] == "luminance"

    lum_rows = [(235, 235, 235, 138.0, 0.3127, 0.3290)]
    lum = _upload_csv(client, sid, lum_rows, name="luminance.csv")
    assert lum.status_code == 200
    assert lum.json()["session"]["peak_luminance"] == 138.0

    to_wb = client.post(f"/api/session/{sid}/next")
    assert to_wb.status_code == 200
    assert to_wb.json()["step"] == "white_balance"
    wb_screen = client.get(f"/api/session/{sid}").json()
    assert wb_screen["wb_data"]["ready_to_continue"] is False

    wb_rows = [
        (188, 188, 188, 90.0, 0.3175, 0.3335),
        (71, 71, 71, 29.0, 0.3095, 0.3255),
    ]
    wb = _upload_csv(client, sid, wb_rows, name="wb.csv")
    assert wb.status_code == 200
    assert wb.json()["session"]["wb_data"]["ready_to_continue"] is True

    to_gamma = client.post(f"/api/session/{sid}/next")
    assert to_gamma.status_code == 200
    assert to_gamma.json()["step"] == "gamma"
    gamma_blocked = client.post(f"/api/session/{sid}/next")
    assert gamma_blocked.status_code == 400

    gamma_rows = [
        (47, 47, 47, 8.5, 0.3127, 0.3290),
        (94, 94, 94, 29.0, 0.3127, 0.3290),
        (141, 141, 141, 62.0, 0.3127, 0.3290),
        (188, 188, 188, 104.0, 0.3127, 0.3290),
    ]
    gamma = _upload_csv(client, sid, gamma_rows, name="gamma.csv")
    assert gamma.status_code == 200

    to_cms = client.post(f"/api/session/{sid}/next")
    assert to_cms.status_code == 200
    assert to_cms.json()["step"] == "color_tuner"

    cms_rows = [
        (235, 0, 0, 29.5, 0.6400, 0.3300),
        (0, 235, 0, 99.9, 0.3000, 0.6000),
        (0, 0, 235, 10.7, 0.1500, 0.0600),
        (0, 235, 235, 110.0, 0.2250, 0.3290),
        (235, 0, 235, 40.2, 0.3210, 0.1540),
        (235, 235, 0, 129.0, 0.4190, 0.5050),
    ]
    cms = _upload_csv(client, sid, cms_rows, name="cms.csv")
    assert cms.status_code == 200
    assert len(cms.json()["session"]["cms_data"]["measurements"]) == 6

    to_post = client.post(f"/api/session/{sid}/next")
    assert to_post.status_code == 200
    assert to_post.json()["step"] == "post_grayscale"

    post = _upload_csv(client, sid, _grayscale_rows(138), name="post_gray.csv")
    assert post.status_code == 200
    assert len(post.json()["session"]["grayscale_data"]["measurements"]) == 11

    report_step = client.post(f"/api/session/{sid}/next")
    assert report_step.status_code == 200
    report_session = report_step.json()
    assert report_session["step"] == "report"
    assert report_session["tv"] == "Hisense U8G"
    assert report_session["date"]

    report = client.get(f"/api/session/{sid}/report")
    assert report.status_code == 200
    payload = report.json()
    assert payload["target"]["peak_nits"] == 140
    assert payload["pre_cal"]["avg_de"] is not None
    assert payload["post_cal"]["avg_de"] is not None
    assert payload["gamma"]["measurements"]
    assert len(payload["cms_measurements"]) == 6

    stop = client.delete("/api/watch/config")
    assert stop.status_code == 200
    assert stop.json()["watching"] is False
