from __future__ import annotations

from calibrator.report_samples import build_sample_report, write_sample_report_artifacts


def test_build_sample_report_is_deterministic():
    report = build_sample_report()

    assert report["tv"] == "Hisense U8G"
    assert report["mode"] == "SDR"
    assert report["date"] == "2026-04-19T09:30:00Z"
    assert report["pre_cal"]["avg_de"] is not None
    assert report["post_cal"]["avg_de"] is not None
    assert report["improvement_pct"] is not None
    assert len(report["pre_cal"]["measurements"]) == 10
    assert len(report["gamma_measurements"]) == 4


def test_write_sample_report_artifacts(tmp_path):
    artifacts = write_sample_report_artifacts(tmp_path, include_pdf=False)

    html = artifacts["html"].read_text(encoding="utf-8")

    assert artifacts["json"].is_file()
    assert artifacts["html"].is_file()
    assert "pdf" not in artifacts
    assert "Calibration Report" in html
    assert "Hisense U8G Calibration Report" in html
