"""Validation and padding of hardware fan curves.

The controller format has exactly six points (temperature in 1/100 °C and
speed in RPM, unsigned 16 bit each).  Users may enter one to six points.  A
curve that cannot be represented exactly is rejected with a reason - it is
never silently altered.  Shorter curves are padded by repeating the last
speed at temperatures 0.01 °C apart (the controller requires strictly
ascending temperatures); the resulting effective curve is returned so it can
be shown.  No hidden safety points are added.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .commander.protocol import CURVE_POINTS

MAX_CENTI = 0xFFFF
MAX_RPM_PROTOCOL = 0xFFFF


class CurveError(ValueError):
    def __init__(self, code: str, **details: object) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def effective_curve(
    points: Sequence[tuple[float, int]], *, max_rpm: int | None
) -> list[tuple[float, int]]:
    """Validate user points and return the six points sent to the controller."""
    if not 1 <= len(points) <= CURVE_POINTS:
        raise CurveError("curve_points_count", min=1, max=CURVE_POINTS, given=len(points))
    if max_rpm is None or max_rpm <= 0:
        raise CurveError("curve_max_rpm_missing")
    if max_rpm > MAX_RPM_PROTOCOL:
        raise CurveError("curve_rpm_range", max=MAX_RPM_PROTOCOL)

    centis: list[int] = []
    rpms: list[int] = []
    for index, (temp, rpm) in enumerate(points):
        if isinstance(temp, bool) or not isinstance(temp, (int, float)) or not math.isfinite(temp):
            raise CurveError("curve_value_not_finite", point=index)
        if isinstance(rpm, bool) or not isinstance(rpm, (int, float)) or not math.isfinite(rpm):
            raise CurveError("curve_value_not_finite", point=index)
        if rpm != int(rpm):
            raise CurveError("curve_rpm_not_integer", point=index)
        centi = round(temp * 100)
        if abs(temp * 100 - centi) > 1e-6:
            raise CurveError("curve_temperature_resolution", point=index, step=0.01)
        if not 0 <= centi <= MAX_CENTI:
            raise CurveError("curve_temperature_range", point=index, min=0, max=MAX_CENTI / 100)
        if not 0 <= rpm <= max_rpm:
            raise CurveError("curve_rpm_range", point=index, min=0, max=max_rpm)
        if centis and centi <= centis[-1]:
            raise CurveError("curve_temperatures_not_ascending", point=index)
        if rpms and rpm < rpms[-1]:
            raise CurveError("curve_rpm_decreasing", point=index)
        centis.append(centi)
        rpms.append(int(rpm))

    if rpms[-1] != max_rpm:
        raise CurveError("curve_last_point_not_max", max_rpm=max_rpm, given=rpms[-1])

    missing = CURVE_POINTS - len(centis)
    if centis[-1] + missing > MAX_CENTI:
        raise CurveError("curve_temperature_range", point=len(centis) - 1, min=0, max=MAX_CENTI / 100)
    for i in range(1, missing + 1):
        centis.append(centis[-1] + 1)
        rpms.append(rpms[-1])
    return [(c / 100, r) for c, r in zip(centis, rpms)]
