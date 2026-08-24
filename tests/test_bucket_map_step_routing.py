"""#636: ZRO imports at select_mode/prepare/suggested_patches/report must not
silently drop every measurement row.

Both `bucket_map_for_session_step` (calibrator/session.py, used by manual ZRO
upload) and its watch-folder duplicate `_bucket_map_for_session_step`
(calibrator/file_watcher.py) only routed grayscale rows for six of the ten
workflow steps; the other four (select_mode, prepare, suggested_patches,
report) produced an all-empty bucket map and the import was discarded.
"""

import calibrator.file_watcher as fw
from calibrator.session import STEPS_ORDER, bucket_map_for_session_step
from calibrator.zro_import import ZROImportResult

# Steps csv_adapter routes to pre_measurements / post_measurements for the
# generic import path (calibrator/csv_adapter.py _PRE_STEPS / _POST_STEPS).
_PRE_STEPS = {"pre_grayscale", "prepare", "select_mode"}
_POST_STEPS = {"post_grayscale", "suggested_patches", "report"}


def _result() -> ZROImportResult:
    grayscale = [{"label": "5%"}, {"label": "10%"}]
    return ZROImportResult(
        pre_measurements=list(grayscale),
        post_measurements=list(grayscale),
        lum_measurements=[{"label": "Luminance"}],
        wb_measurements=[{"label": "WB Gain (80% gray)"}],
        gamma_measurements=[{"label": "Gamma 30%"}],
        cms_measurements=[{"label": "Red"}],
        grayscale_passes=[grayscale],
    )


class TestSessionBucketMapCoversEveryStep:
    def test_no_step_produces_an_all_empty_bucket_map(self):
        for step in STEPS_ORDER:
            bucket_map = bucket_map_for_session_step(_result(), step)
            assert any(bucket_map.values()), f"step {step!r} routed nothing"

    def test_pre_steps_route_to_pre_measurements(self):
        for step in _PRE_STEPS:
            bucket_map = bucket_map_for_session_step(_result(), step)
            assert bucket_map["pre_measurements"], step
            assert not bucket_map["post_measurements"], step

    def test_post_steps_route_to_post_measurements(self):
        for step in _POST_STEPS:
            bucket_map = bucket_map_for_session_step(_result(), step)
            assert bucket_map["post_measurements"], step
            assert not bucket_map["pre_measurements"], step


class TestFileWatcherBucketMapCoversEveryStep:
    def test_no_step_produces_an_all_empty_bucket_map(self):
        for step in STEPS_ORDER:
            bucket_map = fw._bucket_map_for_session_step(_result(), step)
            assert any(bucket_map.values()), f"step {step!r} routed nothing"

    def test_pre_steps_route_to_pre_measurements(self):
        for step in _PRE_STEPS:
            bucket_map = fw._bucket_map_for_session_step(_result(), step)
            assert bucket_map["pre_measurements"], step
            assert not bucket_map["post_measurements"], step

    def test_post_steps_route_to_post_measurements(self):
        for step in _POST_STEPS:
            bucket_map = fw._bucket_map_for_session_step(_result(), step)
            assert bucket_map["post_measurements"], step
            assert not bucket_map["pre_measurements"], step


class TestManualAndWatcherBucketMapsAgree:
    """Parity: the two importers must classify every step identically."""

    def test_identical_bucket_map_for_every_step(self):
        for step in STEPS_ORDER + [None, "unknown_step"]:
            manual = bucket_map_for_session_step(_result(), step)
            watcher = fw._bucket_map_for_session_step(_result(), step)
            assert manual == watcher, step
