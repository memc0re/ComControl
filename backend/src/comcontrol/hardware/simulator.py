"""Byte-level simulation of a Commander PRO.

The simulator answers the same 64-byte requests as the real controller, so the
whole driver (`CommanderPro`), the adapter and everything above it run
unchanged against it.  Behaviour that was observed on real hardware (see
docs/protocol.md) is reproduced; behaviour that was *not* verified (e.g. a curve
whose sensor disappears) is a simulator choice and must never be presented as
hardware-confirmed.

Simulation is only ever used when explicitly selected.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field

from ..commander import protocol as p
from ..commander.hidraw import TransportError
from ..commander.protocol import Command

# Observed: after SET_FAN_MODE 0 ("off") the mode read-back is 3 (undocumented).
MODE_OFF_READBACK = 3


@dataclass
class SimFan:
    detected: int = 0  # 0 = nothing connected, 1 = DC, 2 = PWM
    max_rpm: int = 1500
    stall_below_percent: int = 0  # DC fans stand still below this power
    percent: int | None = 100
    curve: tuple[int, list[tuple[int, int]]] | None = None  # sensor, [(centi °C, rpm)]
    mode: int = 0  # current mode as reported by 0x20
    blocked: bool = False  # fault: rotor blocked -> 0 rpm
    last_rpm: int = 0

    def __post_init__(self) -> None:
        self.mode = self.detected


@dataclass
class SimSensor:
    connected: bool = False
    temperature: float = 24.0


@dataclass
class SimFaults:
    unplugged: bool = False        # every exchange fails like a removed device
    corrupt_responses: int = 0     # next N responses have a wrong length
    status_error_reads: int = 0    # next N temperature reads answer with status 0x10


@dataclass
class SimulatedCommander:
    """Virtual controller.  Thread-safe; one exchange at a time."""

    preset: str = "custom"
    fans: list[SimFan] = field(default_factory=lambda: [SimFan() for _ in range(p.NUM_FANS)])
    sensors: list[SimSensor] = field(
        default_factory=lambda: [SimSensor() for _ in range(p.NUM_TEMP_SENSORS)]
    )
    rails_mv: tuple[int, int, int] = (12_080, 5_020, 3_310)
    firmware: tuple[int, int, int] = (0, 9, 212)
    bootloader: tuple[int, int] = (0, 5)
    animate: bool = False  # slow temperature drift and rpm jitter for demos
    faults: SimFaults = field(default_factory=SimFaults)
    sent: list[bytes] = field(default_factory=list)  # every accepted request

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._rng = random.Random(4711)
        self._t0 = time.monotonic()

    # --- transport interface ---------------------------------------
    def exchange(self, frame: bytes) -> bytes:
        with self._lock:
            if self.faults.unplugged:
                raise TransportError("I/O error: No such device (simulated)")
            if len(frame) != p.REPORT_LENGTH:
                raise ValueError("frame must be 64 bytes")
            self.sent.append(bytes(frame))
            response = self._handle(frame)
            if self.faults.corrupt_responses > 0:
                self.faults.corrupt_responses -= 1
                return response[:-1]
            return response

    def close(self) -> None:
        pass

    # --- helpers ------------------------------------------------------
    @staticmethod
    def _ok(*data: int) -> bytes:
        return bytes([0, *data]).ljust(p.RESPONSE_LENGTH, b"\x00")

    @staticmethod
    def _status(code: int) -> bytes:
        return bytes([code]).ljust(p.RESPONSE_LENGTH, b"\x00")

    def _temp(self, index: int) -> float:
        sensor = self.sensors[index]
        if not self.animate:
            return sensor.temperature
        t = time.monotonic() - self._t0
        return sensor.temperature + 0.8 * math.sin(t / 90 + index)

    def _rpm(self, index: int) -> int:
        fan = self.fans[index]
        if fan.mode not in (1, 2) or fan.blocked:
            return 0
        if fan.curve is not None:
            sensor, points = fan.curve
            if not self.sensors[sensor].connected:
                return fan.last_rpm  # simulator choice, hardware behaviour unverified
            temp = round(self._temp(sensor) * 100)
            target = _interpolate(points, temp)
        else:
            pct = fan.percent or 0
            if pct == 0 or (fan.mode == 1 and pct < fan.stall_below_percent):
                target = 0
            else:
                target = round(fan.max_rpm * pct / 100)
        target = min(target, fan.max_rpm)
        if self.animate and target:
            target = max(0, target + self._rng.randint(-15, 15))
        fan.last_rpm = target
        return target

    def _handle(self, frame: bytes) -> bytes:
        cmd, a, b, c = frame[0], frame[1], frame[2], frame[3]
        if cmd == Command.GET_FIRMWARE:
            return self._ok(*self.firmware)
        if cmd == Command.GET_BOOTLOADER:
            return self._ok(*self.bootloader)
        if cmd == Command.GET_TEMP_CONNECTED:
            return self._ok(*(int(s.connected) for s in self.sensors))
        if cmd == Command.GET_TEMP:
            if a >= p.NUM_TEMP_SENSORS:
                return self._status(0x10)
            if not self.sensors[a].connected:
                return self._status(0x11)
            if self.faults.status_error_reads > 0:
                self.faults.status_error_reads -= 1
                return self._status(0x10)
            raw = round(self._temp(a) * 100) & 0xFFFF
            return self._ok(raw >> 8, raw & 0xFF)
        if cmd == Command.GET_VOLTAGE:
            if a > 2:
                return self._status(0x10)
            mv = self.rails_mv[a]
            return self._ok(mv >> 8, mv & 0xFF)
        if cmd == Command.GET_FAN_MODES:
            return self._ok(*(f.mode for f in self.fans))
        if cmd == Command.GET_FAN_RPM:
            if a >= p.NUM_FANS:
                return self._status(0x10)
            rpm = self._rpm(a)
            return self._ok(rpm >> 8, rpm & 0xFF)
        if cmd == Command.SET_FAN_PERCENT:
            if a >= p.NUM_FANS or b > 100:
                return self._status(0x10)
            if self.fans[a].mode not in (1, 2):
                return self._status(0x12)
            self.fans[a].percent, self.fans[a].curve = b, None
            return self._ok()
        if cmd == Command.SET_FAN_CURVE:
            if a >= p.NUM_FANS or b >= p.NUM_TEMP_SENSORS:
                return self._status(0x10)
            if self.fans[a].mode not in (1, 2):
                return self._status(0x12)
            temps = [(frame[3 + 2 * i] << 8) | frame[4 + 2 * i] for i in range(6)]
            rpms = [(frame[15 + 2 * i] << 8) | frame[16 + 2 * i] for i in range(6)]
            self.fans[a].curve = (b, list(zip(temps, rpms)))
            self.fans[a].percent = None
            return self._ok()
        if cmd == Command.SET_FAN_MODE:
            if a != 0x02 or b >= p.NUM_FANS or c > 2:
                return self._status(0x10)
            fan = self.fans[b]
            if c == 0:
                fan.mode = MODE_OFF_READBACK
            elif fan.detected:
                fan.mode = c
            return self._ok()
        return self._status(0x01)


def _interpolate(points: list[tuple[int, int]], temp: int) -> int:
    if temp <= points[0][0]:
        return points[0][1]
    for (t0, r0), (t1, r1) in zip(points, points[1:]):
        if temp <= t1:
            return round(r0 + (r1 - r0) * (temp - t0) / (t1 - t0)) if t1 != t0 else r1
    return points[-1][1]


def _fan(kind: str, max_rpm: int, stall: int = 0) -> SimFan:
    return SimFan(detected={"dc": 1, "pwm": 2}[kind], max_rpm=max_rpm, stall_below_percent=stall)


PRESETS = ("empty", "minimal", "gaps", "full")


def make_preset(name: str, *, animate: bool = False) -> SimulatedCommander:
    """Build a simulated controller for one of the documented configurations."""
    sim = SimulatedCommander(preset=name, animate=animate)
    if name == "empty":
        pass
    elif name == "minimal":  # one PWM fan, one sensor (like the dev setup)
        sim.fans[0] = _fan("pwm", 1500)
        sim.sensors[0] = SimSensor(True, 24.0)
    elif name == "gaps":  # several channels with gaps, one DC fan
        sim.fans[0] = _fan("pwm", 1500)
        sim.fans[2] = _fan("dc", 7400, stall=45)
        sim.fans[5] = _fan("pwm", 2000)
        sim.sensors[0] = SimSensor(True, 25.0)
        sim.sensors[2] = SimSensor(True, 31.5)
    elif name == "full":
        for i in range(p.NUM_FANS):
            sim.fans[i] = _fan("dc", 2200, stall=40) if i % 3 == 2 else _fan("pwm", 1500 + 100 * i)
        for i in range(p.NUM_TEMP_SENSORS):
            sim.sensors[i] = SimSensor(True, 22.0 + 3 * i)
    else:
        raise ValueError(f"unknown simulator preset {name!r}; choose one of {', '.join(PRESETS)}")
    return sim
