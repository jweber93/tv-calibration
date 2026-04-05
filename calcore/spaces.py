from __future__ import annotations

from typing import Sequence, Tuple

BT709_RGB_TO_XYZ = (
    (0.4123908, 0.35758434, 0.18048079),
    (0.21263901, 0.71516868, 0.07219232),
    (0.01933082, 0.11919478, 0.95053215),
)

P3D65_RGB_TO_XYZ = (
    (0.48657095, 0.26566769, 0.19821729),
    (0.22897456, 0.69173852, 0.07928691),
    (0.0, 0.04511338, 1.04394437),
)

BT2020_RGB_TO_XYZ = (
    (0.636958048, 0.144616904, 0.168880975),
    (0.262700212, 0.677998071, 0.059301717),
    (0.0, 0.028072693, 1.060985057),
)


def rgb_to_xyz(
    rgb: Tuple[float, float, float],
    matrix: Sequence[Sequence[float]],
) -> Tuple[float, float, float]:
    r, g, b = rgb
    x = 100.0 * (matrix[0][0] * r + matrix[0][1] * g + matrix[0][2] * b)
    y = 100.0 * (matrix[1][0] * r + matrix[1][1] * g + matrix[1][2] * b)
    z = 100.0 * (matrix[2][0] * r + matrix[2][1] * g + matrix[2][2] * b)
    return (x, y, z)


def detect_matrix(name: str):
    key = name.lower()
    if key in ("bt709", "709", "rec709"):
        return BT709_RGB_TO_XYZ
    if key in ("p3d65", "displayp3", "p3"):
        return P3D65_RGB_TO_XYZ
    if key in ("bt2020", "2020", "rec2020"):
        return BT2020_RGB_TO_XYZ
    raise ValueError(f"Unknown target space: {name}")
