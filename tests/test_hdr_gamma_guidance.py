"""Tests that HDR gamma guidance tracks the PQ reference, not a 2.2 gamma.

Regression coverage for the bug where ``gamma_from_luminance`` (a plain
power-law exponent) was used for PQ/HDR sessions, producing meaningless
effective-gamma values and comparing them against a 2.2 / 0.0 target.

Also covers the firmware tone-mapping knee guard (issue #548): PQ points whose
ST.2084 reference luminance exceeds the panel peak must emit "hold" instead of
chasing an unreachable reference with menu controls.
"""
from calcore.eotf import pq_eotf
from calcore.models import Measurement
from calibrator.guidance import (
    GAMMA_TRACKING_LEVELS,
    gamma_recommendations,
    preset_gamma_control_plan,
    u8g_gamma_control_plan,
)
from calibrator.utils import pq_point_above_knee

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
        # Below-knee points (20/40/60%) are genuinely too dark (effective
        # tracking > 1.0) -> brighten (raise) them.
        below_knee = [p for p in plan if p["control"] != "80%"]
        assert below_knee
        assert all(p["direction"] == "up" for p in below_knee)
        # 80% PQ reference (~1555 nits) exceeds the 1000-nit panel peak, so it
        # is in the firmware tone-mapping region and must hold, not raise.
        knee = next(p for p in plan if p["control"] == "80%")
        assert knee["direction"] == "hold"
        assert "tone mapping" in knee["reason"].lower()


class TestU8gControlPlanPqKnee:
    """Regression tests for issue #548: the firmware tone-mapping knee guard."""

    def test_sub_peak_panel_holds_above_knee_points(self):
        # 500-nit measured peak. PQ references: 70% ~621 nits, 80% ~1555 nits
        # — both exceed the panel peak, so a correctly tone-mapping panel reads
        # as "too dark" vs the unreachable reference. The plan must hold these
        # points instead of emitting "up" forever. Below-knee points (20/40/60%,
        # refs 2.4 / 32.4 / 244 nits) track perfectly and hold via the normal
        # path — genuine midtone errors below the knee are not suppressed.
        peak = 500.0
        levels = (20, 40, 60, 70, 80)
        meas = [
            Measurement(
                Y=min(pq_eotf(n / 100.0), peak),  # correct hard-clip tone map
                label=f"Gamma {n}%",
                stimulus_rgb=(round(n / 100.0 * 1023),) * 3,
            )
            for n in levels
        ]
        plan = u8g_gamma_control_plan(
            meas,
            target_gamma=0.0,
            peak_nits=peak,
            signal_range="full",
            code_scale="10bit",
            levels=levels,
            eotf=PQ_EOTF,
        )
        by_label = {p["control"]: p for p in plan}
        # 70% and 80% are above the knee -> hold via the knee guard (not "up").
        assert by_label["70%"]["direction"] == "hold"
        assert by_label["80%"]["direction"] == "hold"
        assert "tone mapping" in by_label["70%"]["reason"].lower()
        assert "tone mapping" in by_label["80%"]["reason"].lower()
        # Below-knee points that track perfectly hold via the normal path.
        assert by_label["20%"]["direction"] == "hold"
        assert by_label["40%"]["direction"] == "hold"
        assert by_label["60%"]["direction"] == "hold"

    def test_below_knee_too_dark_still_corrected(self):
        # A genuine midtone error below the knee must still produce "up", not
        # be masked by the knee guard.
        peak = 700.0
        levels = (20, 40, 60)
        meas = [
            Measurement(
                Y=pq_eotf(n / 100.0) * 0.5,  # half the reference -> too dark
                label=f"Gamma {n}%",
                stimulus_rgb=(round(n / 100.0 * 1023),) * 3,
            )
            for n in levels
        ]
        plan = u8g_gamma_control_plan(
            meas,
            target_gamma=0.0,
            peak_nits=peak,
            signal_range="full",
            code_scale="10bit",
            levels=levels,
            eotf=PQ_EOTF,
        )
        assert plan
        assert all(p["direction"] == "up" for p in plan)

    def test_knee_helper_boundary(self):
        # 1000-nit panel: 80% (~1555 nits) is above knee, 70% (~621) is not.
        assert pq_point_above_knee(80, 1000.0) is True
        assert pq_point_above_knee(70, 1000.0) is False
        # 500-nit panel: 70% (~621) is above knee, 60% (~244) is not.
        assert pq_point_above_knee(70, 500.0) is True
        assert pq_point_above_knee(60, 500.0) is False
        # Degenerate inputs never claim above-knee.
        assert pq_point_above_knee(80, 0.0) is False
        assert pq_point_above_knee(0, 1000.0) is False
        assert pq_point_above_knee(100, 1000.0) is False

    def test_above_knee_failed_reading_omitted(self):
        # A failed/missing reading (Y=0 -> eff_gamma None) at an above-knee
        # point must be omitted from the plan, not reported as a confirmed
        # "hold". The shared None guard skips it before the knee branch.
        peak = 500.0
        levels = (20, 80)
        meas = [
            Measurement(
                Y=pq_eotf(0.20),  # below-knee, perfect -> hold (normal path)
                label="Gamma 20%",
                stimulus_rgb=(round(0.20 * 1023),) * 3,
            ),
            Measurement(
                Y=0.0,  # failed reading at an above-knee point
                label="Gamma 80%",
                stimulus_rgb=(round(0.80 * 1023),) * 3,
            ),
        ]
        plan = u8g_gamma_control_plan(
            meas,
            target_gamma=0.0,
            peak_nits=peak,
            signal_range="full",
            code_scale="10bit",
            levels=levels,
            eotf=PQ_EOTF,
        )
        labels = [p["control"] for p in plan]
        assert "20%" in labels
        assert "80%" not in labels  # failed reading omitted, not "hold"


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


class TestPqKneeAggregate:
    """Issue #548: aggregate advice must not chase the tone-mapping wall."""

    def _clipped_pass(self, peak: float, levels=GAMMA_TRACKING_LEVELS):
        return [
            Measurement(
                Y=min(pq_eotf(n / 100.0), peak),
                label=f"Gamma {n}%",
                stimulus_rgb=(round(n / 100.0 * 1023),) * 3,
            )
            for n in levels
        ]

    def test_recommendations_no_highlight_rec_for_clipped_peak(self):
        # 700-nit panel, correctly tone-mapped 80% point (PQ ref ~1555 nits,
        # above the knee). The "highlights too dark — raise 70%/80%"
        # recommendation must not fire because the clipped 80% point is
        # excluded from the aggregate.
        recs = gamma_recommendations(
            self._clipped_pass(700.0),
            target_gamma=0.0,
            peak_nits=700.0,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        joined = " ".join(recs).lower()
        assert "raise 70%/80%" not in joined
        assert "highlights too dark" not in joined

    def test_preset_plan_holds_for_clipped_peak(self):
        # 700-nit panel, correctly tone-mapped. The preset plan must not
        # recommend a higher preset because the clipped 80% point reads dark.
        plan = preset_gamma_control_plan(
            self._clipped_pass(700.0),
            target_gamma=0.0,
            peak_nits=700.0,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        assert plan[0]["direction"] == "hold"

    def test_recommendations_all_above_knee_message(self):
        # Pathological: panel peak so low every tracked point is above the knee
        # (20% PQ ref ~2.4 nits > 1-nit peak).
        recs = gamma_recommendations(
            self._clipped_pass(1.0),
            target_gamma=0.0,
            peak_nits=1.0,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        assert any("tone-mapping" in r.lower() for r in recs)

    def test_recommendations_failed_readings_not_blamed_on_tone_mapping(self):
        # All readings failed (Y=0 -> eff_gamma None) at below-knee points on a
        # 1000-nit panel (no point is above the knee). The empty-gammas fallback
        # must NOT claim the tone-mapping wall is the cause — the operator should
        # be told to re-measure, not that firmware tone mapping is active.
        levels = (20, 40, 60)
        meas = [
            Measurement(
                Y=0.0,
                label=f"Gamma {n}%",
                stimulus_rgb=(round(n / 100.0 * 1023),) * 3,
            )
            for n in levels
        ]
        recs = gamma_recommendations(
            meas,
            target_gamma=0.0,
            peak_nits=1000.0,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        joined = " ".join(recs).lower()
        assert "tone-mapping" not in joined
        assert "take another gamma reading" in joined

    def test_preset_plan_failed_readings_return_empty(self):
        # Failed readings at below-knee points (no above-knee points seen):
        # preset plan returns [] (not the tone-mapping hold message).
        levels = (20, 40, 60)
        meas = [
            Measurement(
                Y=0.0,
                label=f"Gamma {n}%",
                stimulus_rgb=(round(n / 100.0 * 1023),) * 3,
            )
            for n in levels
        ]
        plan = preset_gamma_control_plan(
            meas,
            target_gamma=0.0,
            peak_nits=1000.0,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        assert plan == []

    def test_recommendations_mixed_failure_and_knee_not_blamed_on_tone_mapping(self):
        # 1000-nit panel, levels (20, 40, 60, 80). 20/40/60% are below the knee
        # but failed to read (Y=0 -> eff_gamma None); 80% is genuinely above
        # the knee (PQ ref ~1555 nits > 1000). Only one of the four points is
        # actually in the tone-mapping region, so the fallback must not claim
        # "All measured gamma points" are there — that would send the operator
        # to raise the panel's HDR peak instead of re-measuring the three
        # points that simply never registered a reading.
        levels = (20, 40, 60, 80)
        meas = [
            Measurement(
                Y=0.0,
                label=f"Gamma {n}%",
                stimulus_rgb=(round(n / 100.0 * 1023),) * 3,
            )
            for n in levels
        ]
        recs = gamma_recommendations(
            meas,
            target_gamma=0.0,
            peak_nits=1000.0,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        joined = " ".join(recs).lower()
        assert "tone-mapping" not in joined
        assert "take another gamma reading" in joined

    def test_preset_plan_mixed_failure_and_knee_returns_empty(self):
        # Same mixed scenario as above: one genuinely above-knee point plus
        # several failed below-knee readings. The preset plan must fall back
        # to [] (re-measure), not the "all in tone-mapping region" hold.
        levels = (20, 40, 60, 80)
        meas = [
            Measurement(
                Y=0.0,
                label=f"Gamma {n}%",
                stimulus_rgb=(round(n / 100.0 * 1023),) * 3,
            )
            for n in levels
        ]
        plan = preset_gamma_control_plan(
            meas,
            target_gamma=0.0,
            peak_nits=1000.0,
            signal_range="full",
            code_scale="10bit",
            eotf=PQ_EOTF,
        )
        assert plan == []
