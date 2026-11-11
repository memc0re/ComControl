"""High-level Commander PRO operations on top of any transport."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from . import protocol as p
from .protocol import Command, FanMode


class Transport(Protocol):
    def exchange(self, frame: bytes) -> bytes: ...


_raw_log = logging.getLogger("comcontrol.commander.raw")


class LoggingTransport:
    """Diagnostic wrapper: logs raw request/response bytes at DEBUG level.

    Frames carry only commands and measurements, never credentials.
    """

    def __init__(self, inner: Transport) -> None:
        self.inner = inner

    def exchange(self, frame: bytes) -> bytes:
        _raw_log.debug("tx %s", frame.rstrip(b"\x00").hex(" ") or "00")
        try:
            response = self.inner.exchange(frame)
        except Exception as exc:
            _raw_log.debug("rx error: %s", exc)
            raise
        _raw_log.debug("rx %s", response.hex(" "))
        return response


class CommanderPro:
    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def _call(self, command: Command, data: Sequence[int] = ()) -> bytes:
        response = self._t.exchange(p.build_frame(command, data))
        return p.check_response(command, response)

    def _send(self, command: Command, frame: bytes) -> bytes:
        return p.check_response(command, self._t.exchange(frame))

    # --- read-only ---------------------------------------------------
    def firmware(self) -> tuple[int, int, int]:
        r = self._call(Command.GET_FIRMWARE)
        return r[1], r[2], r[3]

    def bootloader(self) -> tuple[int, int]:
        r = self._call(Command.GET_BOOTLOADER)
        return r[1], r[2]

    def temp_connected(self) -> list[bool]:
        r = self._call(Command.GET_TEMP_CONNECTED)
        return [bool(r[1 + i]) for i in range(p.NUM_TEMP_SENSORS)]

    def fan_modes(self) -> list[FanMode | int]:
        r = self._call(Command.GET_FAN_MODES)
        out: list[FanMode | int] = []
        for i in range(p.NUM_FANS):
            try:
                out.append(FanMode(r[1 + i]))
            except ValueError:
                out.append(r[1 + i])  # unknown value, keep raw
        return out

    def temperature(self, sensor: int) -> float:
        p._check_index("sensor", sensor, p.NUM_TEMP_SENSORS)
        return p.s16be(self._call(Command.GET_TEMP, [sensor])) / 100

    def fan_rpm(self, fan: int) -> int:
        p._check_index("fan", fan, p.NUM_FANS)
        return p.u16be(self._call(Command.GET_FAN_RPM, [fan]))

    def voltage(self, rail: int) -> float:
        p._check_index("rail", rail, 3)  # 0=12V 1=5V 2=3.3V
        return p.u16be(self._call(Command.GET_VOLTAGE, [rail])) / 1000

    # --- writing -----------------------------------------------------
    def set_fan_mode(self, fan: int, mode: FanMode) -> None:
        self._send(Command.SET_FAN_MODE, p.build_set_mode(fan, mode))

    def set_fixed_percent(self, fan: int, percent: int) -> None:
        self._send(Command.SET_FAN_PERCENT, p.build_set_percent(fan, percent))

    def set_curve(self, fan: int, sensor: int, points: Sequence[tuple[float, int]]) -> None:
        self._send(Command.SET_FAN_CURVE, p.build_set_curve(fan, sensor, points))
