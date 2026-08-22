from __future__ import annotations

import math
from typing import Dict, Mapping, Tuple

D65_XYZ = (95.047, 100.0, 108.883)
D65_xy = (0.3127, 0.3290)
D65_XY = D65_xy

_COLOUR_RGB_MIX: Dict[str, Tuple[float, float, float]] = {
    "Red": (1.0, 0.0, 0.0),
    "Green": (0.0, 1.0, 0.0),
    "Blue": (0.0, 0.0, 1.0),
    "Cyan": (0.0, 1.0, 1.0),
    "Magenta": (1.0, 0.0, 1.0),
    "Yellow": (1.0, 1.0, 0.0),
}


def _solve_linear_3x3(
    m: Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]],
    b: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    """Solve the 3x3 linear system m @ x = b via Cramer's rule (no numpy dependency)."""

    def det3(mat: Tuple[Tuple[float, float, float], ...]) -> float:
        return (
            mat[0][0] * (mat[1][1] * mat[2][2] - mat[1][2] * mat[2][1])
            - mat[0][1] * (mat[1][0] * mat[2][2] - mat[1][2] * mat[2][0])
            + mat[0][2] * (mat[1][0] * mat[2][1] - mat[1][1] * mat[2][0])
        )

    d = det3(m)
    if d == 0:
        raise ValueError("Singular primaries/white-point matrix — degenerate colour space")

    def replace_col(col: int, vec: Tuple[float, float, float]):
        return tuple(
            tuple(vec[i] if j == col else m[i][j] for j in range(3))
            for i in range(3)
        )

    return (
        det3(replace_col(0, b)) / d,
        det3(replace_col(1, b)) / d,
        det3(replace_col(2, b)) / d,
    )


def target_xy_for_colour(
    primaries: Mapping[str, Tuple[float, float]],
    white_xy: Tuple[float, float],
    colour_name: str,
) -> Tuple[float, float]:
    """Return the additive-mix chromaticity of a primary or secondary colour.

    Builds the RGB→XYZ matrix implied by ``primaries`` + ``white_xy`` (so the
    white point renormalises to unit luminance), then sums the channel(s)
    that make up ``colour_name`` — one channel for a primary (Red/Green/
    Blue), two for a secondary (Cyan/Magenta/Yellow) — and renormalises the
    result to xy.

    A secondary's chromaticity is the luminance-weighted additive mix of two
    primaries, *not* the arithmetic midpoint of their xy coordinates — the
    two differ by up to ~0.08 in xy for real primary sets. This is the single
    source of truth for target chromaticities; both the gamut feasibility
    check (``calcore.gamut``) and the CMS ΔE targets used elsewhere
    (``calibrator.guidance``) must derive secondaries the same way, or the
    two subsystems can disagree on whether a given panel is reachable.
    """
    xr, yr = primaries["red"]
    xg, yg = primaries["green"]
    xb, yb = primaries["blue"]
    xw, yw = white_xy

    Xr, Yr, Zr = xr / yr, 1.0, (1 - xr - yr) / yr
    Xg, Yg, Zg = xg / yg, 1.0, (1 - xg - yg) / yg
    Xb, Yb, Zb = xb / yb, 1.0, (1 - xb - yb) / yb
    m = ((Xr, Xg, Xb), (Yr, Yg, Yb), (Zr, Zg, Zb))
    Xw, Yw, Zw = xw / yw, 1.0, (1 - xw - yw) / yw

    s = _solve_linear_3x3(m, (Xw, Yw, Zw))
    rgb_to_xyz = tuple(tuple(m[i][j] * s[j] for j in range(3)) for i in range(3))

    colour_rgb = _COLOUR_RGB_MIX.get(colour_name, (1.0, 1.0, 1.0))
    X = sum(rgb_to_xyz[0][j] * colour_rgb[j] for j in range(3))
    Y = sum(rgb_to_xyz[1][j] * colour_rgb[j] for j in range(3))
    Z = sum(rgb_to_xyz[2][j] * colour_rgb[j] for j in range(3))
    total = X + Y + Z
    return (round(X / total, 4), round(Y / total, 4)) if total > 0 else white_xy


def f_lab(t: float) -> float:
    delta = (6 / 29) ** 3
    if t > delta:
        return t ** (1 / 3)
    return ((29 / 6) ** 2) * t / 3 + 4 / 29


def xyz_to_lab(
    xyz: Tuple[float, float, float],
    white: Tuple[float, float, float] = D65_XYZ,
) -> Tuple[float, float, float]:
    x, y, z = xyz
    xn, yn, zn = white
    fx = f_lab(x / xn)
    fy = f_lab(y / yn)
    fz = f_lab(z / zn)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def xyY_to_xyz(x: float, y: float, Y: float) -> Tuple[float, float, float]:
    if y <= 0:
        if Y == 0:
            return (0.0, 0.0, 0.0)
        # y=0 is outside the chromaticity diagram; fall back to D65 chromaticity
        x_proj, y_proj = D65_xy
        X = (x_proj * Y) / y_proj
        Z = ((1 - x_proj - y_proj) * Y) / y_proj
        return (X, Y, Z)
    X = (x * Y) / y
    Z = ((1 - x - y) * Y) / y
    return (X, Y, Z)


def ciede2000(
    lab1: Tuple[float, float, float],
    lab2: Tuple[float, float, float],
) -> float:
    # Standard CIEDE2000, kL = kC = kH = 1
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2

    C1 = math.sqrt(a1 * a1 + b1 * b1)
    C2 = math.sqrt(a2 * a2 + b2 * b2)
    Cbar = (C1 + C2) / 2
    Cbar7 = Cbar ** 7
    G = 0.5 * (1 - math.sqrt(Cbar7 / (Cbar7 + 25 ** 7))) if Cbar > 0 else 0.0

    a1p = (1 + G) * a1
    a2p = (1 + G) * a2
    C1p = math.sqrt(a1p * a1p + b1 * b1)
    C2p = math.sqrt(a2p * a2p + b2 * b2)

    def hp(a: float, b: float) -> float:
        if a == 0 and b == 0:
            return 0.0
        ang = math.degrees(math.atan2(b, a))
        return ang + 360 if ang < 0 else ang

    h1p = hp(a1p, b1)
    h2p = hp(a2p, b2)

    dLp = L2 - L1
    dCp = C2p - C1p

    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dh = h2p - h1p
        if dh > 180:
            dh -= 360
        elif dh < -180:
            dh += 360
        dhp = dh

    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))

    Lp_bar = (L1 + L2) / 2
    Cp_bar = (C1p + C2p) / 2

    if C1p * C2p == 0:
        hp_bar = h1p + h2p
    else:
        dh = abs(h1p - h2p)
        if dh > 180:
            hp_bar = (
                (h1p + h2p + 360) / 2
                if (h1p + h2p) < 360
                else (h1p + h2p - 360) / 2
            )
        else:
            hp_bar = (h1p + h2p) / 2

    # Hue-quadrant weighting function T, per Sharma, Wu & Dalal (2005),
    # "The CIEDE2000 Color-Difference Formula: Implementation Notes,
    # Supplementary Test Data, and Mathematical Observations", Color
    # Research & Application 30(1), eq. 4 — fourth-term phase offset is -63°.
    # Verified against the paper's published 34-pair reference table in
    # tests/test_calcore/test_ciede2000_reference.py.
    T = (
        1
        - 0.17 * math.cos(math.radians(hp_bar - 30))
        + 0.24 * math.cos(math.radians(2 * hp_bar))
        + 0.32 * math.cos(math.radians(3 * hp_bar + 6))
        - 0.20 * math.cos(math.radians(4 * hp_bar - 63))
    )

    d_ro = 30 * math.exp(-(((hp_bar - 275) / 25) ** 2))
    RC = math.sqrt((Cp_bar ** 7) / (Cp_bar ** 7 + 25 ** 7)) if Cp_bar > 0 else 0.0
    SL = 1 + (0.015 * ((Lp_bar - 50) ** 2)) / math.sqrt(20 + ((Lp_bar - 50) ** 2))
    SC = 1 + 0.045 * Cp_bar
    SH = 1 + 0.015 * Cp_bar * T
    RT = -2 * RC * math.sin(math.radians(2 * d_ro))

    return math.sqrt(
        (dLp / SL) ** 2
        + (dCp / SC) ** 2
        + (dHp / SH) ** 2
        + RT * (dCp / SC) * (dHp / SH)
    )


def xyY_to_XYZ(x: float, y: float, Y: float) -> Tuple[float, float, float]:
    return xyY_to_xyz(x, y, Y)


def XYZ_to_lab(
    X: float,
    Y: float,
    Z: float,
    ref_white: Tuple[float, float, float] = D65_XYZ,
) -> Tuple[float, float, float]:
    return xyz_to_lab((X, Y, Z), ref_white)


def xyY_to_lab(
    x: float,
    y: float,
    Y_nits: float,
    ref_nits: float = 100.0,
) -> Tuple[float, float, float]:
    Y_norm = Y_nits / ref_nits if ref_nits > 0 else Y_nits
    X, Y_val, Z = xyY_to_xyz(x, y, Y_norm * 100.0)
    return xyz_to_lab((X, Y_val, Z))


def delta_e_cie76(
    x1: float,
    y1: float,
    Y1: float,
    x2: float,
    y2: float,
    Y2: float,
) -> float:
    lab1 = xyY_to_lab(x1, y1, Y1)
    lab2 = xyY_to_lab(x2, y2, Y2)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(lab1, lab2)))
