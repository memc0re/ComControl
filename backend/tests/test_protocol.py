import pytest

from comcontrol.commander import protocol as p
from comcontrol.commander.device import CommanderPro
from comcontrol.commander.protocol import Command, FanMode


class FakeTransport:
    """Records frames; answers from a queue (or a status-0 zero frame)."""

    def __init__(self, *responses: bytes) -> None:
        self.sent: list[bytes] = []
        self.responses = list(responses)

    def exchange(self, frame: bytes) -> bytes:
        self.sent.append(frame)
        return self.responses.pop(0) if self.responses else bytes(p.RESPONSE_LENGTH)


def resp(*data: int, status: int = 0) -> bytes:
    return bytes([status, *data]).ljust(p.RESPONSE_LENGTH, b"\x00")


def test_frame_is_64_bytes_and_zero_padded():
    f = p.build_frame(Command.GET_TEMP, [2])
    assert len(f) == 64 and f[:3] == b"\x11\x02\x00" and not any(f[3:])


def test_unknown_command_is_unbuildable():
    for forbidden in (0x34, 0x35, 0x33, 0xFF, 0x00):  # RGB, unknown
        with pytest.raises(ValueError):
            p.build_frame(forbidden)


def test_whitelist_has_no_led_or_flash_commands():
    assert {c.value for c in Command} == {
        0x02, 0x06, 0x10, 0x11, 0x12, 0x20, 0x21, 0x23, 0x25, 0x28,
    }


@pytest.mark.parametrize("bad", [b"", b"\x00" * 15, b"\x00" * 17])
def test_wrong_response_length_rejected(bad):
    with pytest.raises(p.ProtocolError):
        p.check_response(Command.GET_TEMP, bad)


def test_status_byte_error_mapped():
    with pytest.raises(p.DeviceStatusError) as e:
        p.check_response(Command.GET_TEMP, resp(status=0x11))
    assert e.value.status == 0x11 and "not connected" in str(e.value)


def test_temperature_signed_centidegrees():
    assert CommanderPro(FakeTransport(resp(0x09, 0x2A))).temperature(0) == 23.46
    assert CommanderPro(FakeTransport(resp(0xFF, 0x38))).temperature(0) == -2.0


def test_fan_rpm_and_voltage():
    assert CommanderPro(FakeTransport(resp(0x05, 0xEB))).fan_rpm(0) == 1515
    assert CommanderPro(FakeTransport(resp(0x30, 0x57))).voltage(0) == 12.375


def test_fan_modes_and_temp_connected():
    dev = CommanderPro(FakeTransport(resp(2, 0, 1, 0, 0, 0), resp(1, 0, 0, 0)))
    assert dev.fan_modes() == [FanMode.PWM, 0, FanMode.DC, 0, 0, 0]
    assert dev.temp_connected() == [True, False, False, False]


def test_missing_values_are_not_zero():
    # a disconnected sensor raises instead of yielding 0 °C
    with pytest.raises(p.DeviceStatusError):
        CommanderPro(FakeTransport(resp(status=0x11))).temperature(1)


def test_set_percent_frame_and_bounds():
    t = FakeTransport()
    CommanderPro(t).set_fixed_percent(0, 100)
    assert t.sent[0][:3] == bytes([0x23, 0, 100])
    for fan, pct in [(6, 10), (-1, 10), (0, 101), (0, -1)]:
        with pytest.raises(ValueError):
            CommanderPro(FakeTransport()).set_fixed_percent(fan, pct)


def test_set_mode_frame():
    t = FakeTransport()
    CommanderPro(t).set_fan_mode(0, FanMode.PWM)
    assert t.sent[0][:4] == bytes([0x28, 0x02, 0, 2])


def test_curve_frame_layout():
    pts = [(20, 500), (30, 800), (40, 1200), (50, 1500), (55, 1800), (60, 2000)]
    f = p.build_set_curve(0, 0, pts)
    assert f[0] == 0x25 and f[1] == 0 and f[2] == 0
    assert f[3:15] == bytes.fromhex("07d0 0bb8 0fa0 1388 157c 1770".replace(" ", ""))
    assert f[15:27] == bytes.fromhex("01f4 0320 04b0 05dc 0708 07d0".replace(" ", ""))


@pytest.mark.parametrize("pts", [
    [(20, 500)] * 5,                                                     # too few
    [(20, 500), (20, 600)] + [(30 + i, 700) for i in range(4)],          # not strictly ascending
    [(20, 900), (30, 800)] + [(40 + i, 1000) for i in range(4)],        # rpm decreasing
    [(20, 500), (30, 70000)] + [(40 + i, 70000) for i in range(4)],     # > 16 bit
])
def test_invalid_curves_rejected(pts):
    with pytest.raises(ValueError):
        p.build_set_curve(0, 0, pts)


def test_write_error_not_reported_as_success():
    dev = CommanderPro(FakeTransport(resp(status=0x01)))
    with pytest.raises(p.DeviceStatusError):
        dev.set_fixed_percent(0, 50)
