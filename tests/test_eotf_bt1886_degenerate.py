"""Tests for bt1886_eotf degenerate case: black floor >= white level."""

import unittest
import warnings

from calcore.eotf import bt1886_eotf


class TestBt1886Degenerate(unittest.TestCase):
    """Degenerate black-floor cases for bt1886_eotf."""

    def test_lb_eq_lw_returns_real_number(self):
        """When lb == lw, fallback returns a real float, not complex."""
        result = bt1886_eotf(0.5, lw=100.0, lb=100.0)
        self.assertIsInstance(result, float)
        self.assertFalse(result != result)  # NaN check

    def test_lb_gt_lw_returns_real_number(self):
        """When lb > lw, fallback returns a real float, not complex."""
        result = bt1886_eotf(0.5, lw=10.0, lb=100.0)
        self.assertIsInstance(result, float)
        self.assertFalse(result != result)  # NaN check

    def test_lb_ge_lw_raises_runtime_warning(self):
        """Degenerate case emits a RuntimeWarning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            bt1886_eotf(0.5, lw=10.0, lb=100.0)
            self.assertEqual(len(caught), 1)
            self.assertTrue(issubclass(caught[0].category, RuntimeWarning))
            self.assertIn("black floor", str(caught[0].message))

    def test_lb_eq_lw_falls_back_to_pure_gamma(self):
        """When lb == lw, result equals lw * v**gamma."""
        v, lw, lb, gamma = 0.5, 100.0, 100.0, 2.4
        expected = lw * (v ** gamma)
        self.assertAlmostEqual(bt1886_eotf(v, lw, lb, gamma=gamma), expected)

    def test_lb_ge_lw_v0_returns_zero(self):
        """At v=0 with lb >= lw, result is zero."""
        result = bt1886_eotf(0.0, lw=10.0, lb=100.0)
        self.assertAlmostEqual(result, 0.0)

    def test_lb_ge_lw_v1_returns_lw(self):
        """At v=1 with lb >= lw, result equals white level."""
        result = bt1886_eotf(1.0, lw=100.0, lb=100.0)
        self.assertAlmostEqual(result, 100.0)

    def test_normal_case_still_works(self):
        """Normal case (lb < lw) is unaffected."""
        result = bt1886_eotf(0.5, lw=200.0, lb=0.1)
        self.assertIsInstance(result, float)
        self.assertFalse(result != result)  # not NaN
        self.assertGreater(result, 0.0)

    def test_normal_case_v0_v1(self):
        """Boundary values in normal case."""
        # At v=0, result = lb (the black floor)
        self.assertAlmostEqual(bt1886_eotf(0.0, lw=200.0, lb=0.1), 0.1)
        result_max = bt1886_eotf(1.0, lw=200.0, lb=0.1)
        self.assertAlmostEqual(result_max, 200.0)

    def test_negative_v_clamped(self):
        """Negative input is clamped to 0."""
        result_neg = bt1886_eotf(-1.0, lw=200.0, lb=0.1)
        result_zero = bt1886_eotf(0.0, lw=200.0, lb=0.1)
        self.assertAlmostEqual(result_neg, result_zero)

    def test_gt_one_v_clamped(self):
        """Input > 1 is clamped to 1."""
        result_gt = bt1886_eotf(2.0, lw=200.0, lb=0.1)
        result_one = bt1886_eotf(1.0, lw=200.0, lb=0.1)
        self.assertAlmostEqual(result_gt, result_one)

    def test_lb_zero_is_not_degenerate(self):
        """lb=0 should not trigger warning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            bt1886_eotf(0.5, lw=100.0, lb=0.0)
            runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
            self.assertEqual(len(runtime_warnings), 0)

    def test_degenerate_does_not_produce_complex(self):
        """Verify no complex number leakage at any v."""
        for v in [0.0, 0.01, 0.1, 0.5, 0.9, 0.99, 1.0]:
            result = bt1886_eotf(v, lw=1.0, lb=50.0)
            self.assertIsInstance(result, float, f"v={v} produced non-float")
            self.assertFalse(result != result, f"v={v} produced NaN")


if __name__ == "__main__":
    unittest.main()
