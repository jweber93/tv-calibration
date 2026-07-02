from __future__ import annotations

import warnings


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
    # ST.2084 is an absolute EOTF: the encoded signal maps directly to nits,
    # not to a fraction of the display's peak. A display can't reproduce
    # nits above its own peak, so the target clips (tone-maps) there.
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
