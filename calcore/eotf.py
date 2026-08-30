from __future__ import annotations

import math
import warnings
from typing import Optional


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def pq_eotf(n: float) -> float:
    # SMPTE ST 2084 inverse EOTF, returns nits
    m1 = 0.1593017578125
    m2 = 78.84375
    c1 = 0.8359375
    c2 = 18.8515625
    c3 = 18.6875
    n = clamp(n, 0.0, 1.0)
    n_m2 = n ** (1 / m2)
    num = max(n_m2 - c1, 0.0)
    den = c2 - c3 * n_m2
    if den <= 0:
        return 10000.0
    return 10000.0 * ((num / den) ** (1 / m1))


def pq_target_nits(n: float, peak_nits: float) -> float:
    """Absolute ST.2084 target luminance for signal *n*, clipped at *peak_nits*.

    ST.2084 is an absolute EOTF: the encoded signal maps directly to nits,
    not to a fraction of the display's peak. A display can't reproduce nits
    above its own peak, so above that point the target is clipped flat at
    *peak_nits* rather than continuing to rise with pq_eotf(n).

    *peak_nits* is whatever peak the caller considers authoritative for this
    comparison — the display's measured peak luminance in `analyze()` and
    `target_xyz_for_patch()`, or the session's configured
    `CalibrationTarget.peak_luminance_nits` in `m_to_dict()`. This is a hard
    clip at that single value, not a soft-rolloff tone-map curve; it doesn't
    model a display's actual (often gradual) knee behaviour below peak.
    """
    return min(pq_eotf(n), peak_nits)


def pq_inverse_eotf(nits: float) -> float:
    # SMPTE ST 2084 forward (OETF): nits -> normalised signal in [0, 1].
    # Inverse of pq_eotf(). nits are clamped to [0, 10000] to stay in the valid
    # PQ range; 0 nits encodes to a tiny positive offset (~7e-7), not exactly 0.
    m1 = 0.1593017578125
    m2 = 78.84375
    c1 = 0.8359375
    c2 = 18.8515625
    c3 = 18.6875
    lp = clamp(nits / 10000.0, 0.0, 1.0)
    lp_m1 = lp ** m1
    num = c1 + c2 * lp_m1
    den = 1.0 + c3 * lp_m1
    return (num / den) ** m2


def bt1886_eotf(v: float, lw: float, lb: float, gamma: float = 2.4) -> float:
    v = clamp(v, 0.0, 1.0)
    lw = max(lw, 1e-6)
    lb = max(lb, 0.0)
    if lb >= lw:
        # Degenerate panel (no dynamic range); fall back to pure gamma.
        warnings.warn(
            "bt1886_eotf: black floor (lb={lb:.4f}) >= white level (lw={lw:.4f}); "
            "falling back to pure gamma.".format(lb=lb, lw=lw),
            RuntimeWarning,
            stacklevel=2,
        )
        return lw * (v ** gamma)
    a = lw ** (1 / gamma) - lb ** (1 / gamma)
    base = a * v + lb ** (1 / gamma)
    return max(base, 0.0) ** gamma


def gamma_eotf(v: float, gamma: float = 2.2) -> float:
    return clamp(v, 0.0, 1.0) ** gamma


def bt1886_gamma_from_luminance(
    measured_nits: float,
    peak_nits: float,
    stimulus_frac: float,
    black_nits: float = 0.0,
    gamma_lo: float = 0.5,
    gamma_hi: float = 8.0,
    tol: float = 1e-7,
    max_iter: int = 80,
) -> Optional[float]:
    """Effective gamma for a measured point, accounting for the BT.1886 black floor.

    A pure through-origin power-law fit (``log(Y/peak) / log(stim)``) implicitly
    assumes ``Y(0) == 0``. Real panels have a non-zero black floor, which the
    BT.1886 EOTF absorbs via its ``lb`` term (see ``bt1886_eotf``) — fitting the
    naive power law to such a panel reports a biased (too-low) gamma even when
    the panel tracks BT.1886 perfectly (issue #637).

    When *black_nits* is 0 (unknown/negligible floor), this reduces to the
    plain power-law estimate, so behaviour is unchanged for callers that don't
    have a measured floor to pass. When *black_nits* is positive, this instead
    solves for the gamma that reproduces the measurement via
    ``bt1886_eotf(stimulus_frac, peak_nits, black_nits, gamma)``, by bisection —
    the BT.1886 formula isn't algebraically invertible for gamma, but for a
    fixed signal in (0, 1) it is monotonically non-increasing in gamma, so
    bisection converges reliably over a generous [gamma_lo, gamma_hi] bracket.

    Returns ``None`` when the inputs are out of range (signal not in (0, 1),
    non-positive peak, or a measurement outside the valid [black, peak] band).
    """
    if peak_nits <= 0 or not (0.0 < stimulus_frac < 1.0) or measured_nits <= 0:
        return None

    if black_nits <= 0:
        rel = measured_nits / peak_nits
        if rel <= 0 or rel > 1.0:
            return None
        return math.log(rel) / math.log(stimulus_frac)

    if measured_nits <= black_nits or measured_nits > peak_nits:
        return None

    def f(gamma: float) -> float:
        return bt1886_eotf(stimulus_frac, peak_nits, black_nits, gamma) - measured_nits

    lo, hi = gamma_lo, gamma_hi
    f_lo, f_hi = f(lo), f(hi)
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        # Measurement falls outside what's reachable in [gamma_lo, gamma_hi];
        # report the closer bound rather than fail silently on a real deviation.
        return lo if abs(f_lo) < abs(f_hi) else hi

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = f(mid)
        if abs(f_mid) <= tol * measured_nits or (hi - lo) < 1e-6:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi, f_hi = mid, f_mid
    return (lo + hi) / 2.0
