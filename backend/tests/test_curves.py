import math

import pytest

from comcontrol.curves import CurveError, effective_curve


def test_six_points_unchanged():
    pts = [(20, 500), (30, 800), (40, 1000), (50, 1200), (55, 1400), (60, 1500)]
    assert effective_curve(pts, max_rpm=1500) == [(float(t), r) for t, r in pts]


def test_short_curve_padded_with_last_point():
    eff = effective_curve([(25, 600), (35, 1500)], max_rpm=1500)
    assert len(eff) == 6
    assert eff[:2] == [(25.0, 600), (35.0, 1500)]
    assert eff[2:] == [(35.01, 1500), (35.02, 1500), (35.03, 1500), (35.04, 1500)]


def test_single_point_curve():
    eff = effective_curve([(40, 1500)], max_rpm=1500)
    assert [r for _, r in eff] == [1500] * 6
    temps = [t for t, _ in eff]
    assert all(b > a for a, b in zip(temps, temps[1:]))


@pytest.mark.parametrize(
    ("pts", "max_rpm", "code"),
    [
        ([], 1500, "curve_points_count"),
        ([(20 + i, 1500) for i in range(7)], 1500, "curve_points_count"),
        ([(20, 500), (30, 1500)], None, "curve_max_rpm_missing"),
        ([(20, 500), (20, 1500)], 1500, "curve_temperatures_not_ascending"),
        ([(30, 500), (20, 1500)], 1500, "curve_temperatures_not_ascending"),
        ([(20, 900), (30, 800), (40, 1500)], 1500, "curve_rpm_decreasing"),
        ([(20, 500), (30, 1400)], 1500, "curve_last_point_not_max"),
        ([(20, 500), (30, 1600)], 1500, "curve_rpm_range"),
        ([(20, -1), (30, 1500)], 1500, "curve_rpm_range"),
        ([(-5, 500), (30, 1500)], 1500, "curve_temperature_range"),
        ([(20.005, 500), (30, 1500)], 1500, "curve_temperature_resolution"),
        ([(20, 500.5), (30, 1500)], 1500, "curve_rpm_not_integer"),
        ([(math.nan, 500), (30, 1500)], 1500, "curve_value_not_finite"),
        ([(20, 500), (math.inf, 1500)], 1500, "curve_value_not_finite"),
        ([(655.34, 1500)], 1500, "curve_temperature_range"),  # no room for padding
        ([(20, 500), (30, 70000)], 70000, "curve_rpm_range"),  # beyond 16 bit
    ],
)
def test_invalid_curves_rejected_with_reason(pts, max_rpm, code):
    with pytest.raises(CurveError) as exc:
        effective_curve(pts, max_rpm=max_rpm)
    assert exc.value.code == code


def test_boundaries_accepted():
    assert effective_curve([(0, 0), (655.29, 1500)], max_rpm=1500)[-1] == (655.33, 1500)
    assert effective_curve([(20.01, 0), (20.02, 65535)], max_rpm=65535)[1] == (20.02, 65535)


def test_padded_curve_is_accepted_by_frame_builder():
    from comcontrol.commander.protocol import build_set_curve

    eff = effective_curve([(25, 600), (35, 1500)], max_rpm=1500)
    frame = build_set_curve(0, 0, eff)
    assert frame[3:5] == bytes.fromhex("09c4")  # 25.00 °C
    assert frame[13:15] == bytes.fromhex("0db0")  # sixth point: 35.04 °C
