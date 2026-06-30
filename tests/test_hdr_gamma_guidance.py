"""Tests that HDR gamma guidance tracks the PQ reference, not a 2.2 gamma.

Regression coverage for the bug where ``gamma_from_luminance`` (a plain
power-law exponent) was used for PQ/HDR sessions, producing meaningless
effective-gamma values and comparing them against a 2.2 / 0.0 target.
"""
from calcore.eotf import pq_eotf
from calcore.models import Measurement
from calibrator.guidance import (
    GAMMA_TRACKING_LEVELS,
    gamma_recommendations,
    preset_gamma_control_plan,
    u8g_gamma_control_plan,
)

PEAK_NITS = 1000.0
PQ_EOTF = "PQ (ST.2084)"


def _pq_measurement(stim_pct: int, scale: float = 1.0) -> Measurement:
    """A grayscale point at ``stim_pct`` whose luminance is ``scale`` x the PQ reference."""
    code = round(stim_pct / 100.0 * 1023)
    return Measurement(
        Y=pq_eotf(stim_pct / 100.0) * scale,
        label=f"Gamma {stim_pct}%",
        stimulus_rgb=(code, code, code),
    )


def _perfect_pq_pass():
    return [_pq_measurement(level) for level in GAMMA_TRACKING_LEVELS]


class TestGammaRecommendationsPq:
    def test_perfect_pq_reports_close(self):
        recs = gamma_recommendations(
            _perfect_pq_pass(),
            target_gamma=0.0,  # HDR targets carry gamma 0
            peak_nits=PEAK_NITS,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        joined = " ".join(recs).lower()
        assert "close" in joined
        assert "too dark" not in joined
        assert "too bright" not in joined

    def test_pq_note_mentions_st2084_reference(self):
        recs = gamma_recommendations(
            _perfect_pq_pass(),
            target_gamma=0.0,
            peak_nits=PEAK_NITS,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        assert any("pq" in r.lower() or "2084" in r.lower() for r in recs)

    def test_pq_too_dark_flagged(self):
        # Every point at half the PQ reference luminance -> too dark.
        meas = [_pq_measurement(level, scale=0.5) for level in GAMMA_TRACKING_LEVELS]
        recs = gamma_recommendations(
            meas,
            target_gamma=0.0,
            peak_nits=PEAK_NITS,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        assert any("too dark" in r.lower() for r in recs)

    def test_sdr_behaviour_unchanged(self):
        # A perfect 2.2 SDR pass should still report "close" without PQ notes.
        meas = []
        for level in GAMMA_TRACKING_LEVELS:
            code = round(level / 100.0 * 255)
            meas.append(
                Measurement(
                    Y=120.0 * (level / 100.0) ** 2.2,
                    label=f"Gamma {level}%",
                    stimulus_rgb=(code, code, code),
                )
            )
        recs = gamma_recommendations(
            meas,
            target_gamma=2.2,
            peak_nits=120.0,
            eotf="BT.1886",
        )
        joined = " ".join(recs).lower()
        assert "close" in joined
        assert "2084" not in joined


class TestU8gControlPlanPq:
    def test_perfect_pq_holds_all_points(self):
        plan = u8g_gamma_control_plan(
            _perfect_pq_pass(),
            target_gamma=0.0,
            peak_nits=PEAK_NITS,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        assert plan
        assert all(p["direction"] == "hold" for p in plan)

    def test_pq_too_dark_raises_points(self):
        meas = [_pq_measurement(level, scale=0.5) for level in GAMMA_TRACKING_LEVELS]
        plan = u8g_gamma_control_plan(
            meas,
            target_gamma=0.0,
            peak_nits=PEAK_NITS,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        # Too dark (effective tracking > 1.0) -> brighten (raise) the points.
        assert plan
        assert all(p["direction"] == "up" for p in plan)


class TestPresetControlPlanPq:
    def test_perfect_pq_holds(self):
        plan = preset_gamma_control_plan(
            _perfect_pq_pass(),
            target_gamma=0.0,
            peak_nits=PEAK_NITS,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        assert plan[0]["direction"] == "hold"
        assert "0.00" not in plan[0]["reason"]  # not comparing against gamma-0 target

    def test_empty_pq_preset_message(self):
        plan = preset_gamma_control_plan(
            [],
            target_gamma=0.0,
            peak_nits=PEAK_NITS,
            eotf=PQ_EOTF,
        )
        assert "pq" in plan[0]["summary"].lower() or "pq" in plan[0]["reason"].lower()
