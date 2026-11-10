"""Pure protocol layer: frame building and response parsing (no I/O).

Wire format: 64-byte output report (command byte first, then arguments, zero
padded) and a 16-byte input report whose byte 0 is a status code.  Only the
commands in `Command` can be built; everything else (RGB, firmware/bootloader
writes, unknown commands) is deliberately unreachable.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence

REPORT_LENGTH = 64
RESPONSE_LENGTH = 16
NUM_FANS = 6
NUM_TEMP_SENSORS = 4
CURVE_POINTS = 6


class Command(enum.IntEnum):
    GET_FIRMWARE = 0x02
    GET_BOOTLOADER = 0x06
    GET_TEMP_CONNECTED = 0x10
    GET_TEMP = 0x11
    GET_VOLTAGE = 0x12
    GET_FAN_MODES = 0x20
    GET_FAN_RPM = 0x21
    SET_FAN_PERCENT = 0x23
    SET_FAN_CURVE = 0x25
    SET_FAN_MODE = 0x28


class FanMode(enum.IntEnum):
    DISCONNECTED = 0
    DC = 1
    PWM = 2


class ProtocolError(Exception):
    """Malformed frame or response."""


class DeviceStatusError(ProtocolError):
    """The controller answered with a non-zero status byte."""

    MESSAGES = {
        0x01: "invalid command",
        0x10: "invalid arguments",
        0x11: "sensor not connected",
        0x12: "fan not controllable in this way",
    }

    def __init__(self, command: Command, status: int) -> None:
        self.command = command
        self.status = status
        text = self.MESSAGES.get(status, "unknown error")
        super().__init__(f"{command.name}: status 0x{status:02x} ({text})")


def _check_index(name: str, value: int, count: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < count:
        raise ValueError(f"{name} must be 0..{count - 1}, got {value!r}")


def build_frame(command: Command, data: Sequence[int] = ()) -> bytes:
    """Build the 64-byte output report (without the hidraw report-id byte)."""
    command = Command(command)  # rejects anything outside the whitelist
    if len(data) > REPORT_LENGTH - 1:
        raise ValueError("too much data for one report")
    if any(not 0 <= b <= 0xFF for b in data):
        raise ValueError("data bytes must be 0..255")
    return bytes([command, *data]).ljust(REPORT_LENGTH, b"\x00")


def check_response(command: Command, response: bytes) -> bytes:
    """Validate length and status byte; return the response unchanged."""
    if len(response) != RESPONSE_LENGTH:
        raise ProtocolError(
            f"{command.name}: expected {RESPONSE_LENGTH} bytes, got {len(response)}"
        )
    if response[0] != 0x00:
        raise DeviceStatusError(command, response[0])
    return response


def u16be(response: bytes, offset: int = 1) -> int:
    return (response[offset] << 8) | response[offset + 1]


def s16be(response: bytes, offset: int = 1) -> int:
    value = u16be(response, offset)
    return value - 0x10000 if value & 0x8000 else value


def build_set_percent(fan: int, percent: int) -> bytes:
    _check_index("fan", fan, NUM_FANS)
    if not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= 100:
        raise ValueError(f"percent must be 0..100, got {percent!r}")
    return build_frame(Command.SET_FAN_PERCENT, [fan, percent])


def build_set_mode(fan: int, mode: FanMode) -> bytes:
    _check_index("fan", fan, NUM_FANS)
    return build_frame(Command.SET_FAN_MODE, [0x02, fan, FanMode(mode)])


def build_set_curve(fan: int, sensor: int, points: Sequence[tuple[float, int]]) -> bytes:
    """Build a fan curve frame: exactly six (temp °C, rpm) points.

    Temperatures must be strictly ascending, rpm non-decreasing.  The caller
    pads shorter curves; this layer never invents points.
    """
    _check_index("fan", fan, NUM_FANS)
    _check_index("sensor", sensor, NUM_TEMP_SENSORS)
    if len(points) != CURVE_POINTS:
        raise ValueError(f"a curve needs exactly {CURVE_POINTS} points, got {len(points)}")
    temps = [round(t * 100) for t, _ in points]
    rpms = [r for _, r in points]
    for i in range(1, CURVE_POINTS):
        if temps[i] <= temps[i - 1]:
            raise ValueError("temperatures must be strictly ascending")
        if rpms[i] < rpms[i - 1]:
            raise ValueError("rpm must not decrease with rising temperature")
    if any(not 0 <= t <= 0xFFFF for t in temps) or any(
        not isinstance(r, int) or not 0 <= r <= 0xFFFF for r in rpms
    ):
        raise ValueError("curve values out of 16-bit range")
    data = [fan, sensor]
    for t in temps:
        data += [t >> 8, t & 0xFF]
    for r in rpms:
        data += [r >> 8, r & 0xFF]
    return build_frame(Command.SET_FAN_CURVE, data)
