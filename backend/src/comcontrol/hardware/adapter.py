"""Hardware adapter: the only code that talks to the controller.

`HardwareAdapter` is synchronous and must only be used from the single
hardware worker thread (see `worker.py`).  It works with any `Connector`:

* `HidrawConnector` – the real Commander PRO at a configured USB port path,
* `SimulatorConnector` – the byte-level simulator, only when explicitly chosen.

There is no fallback from one to the other: a hardware error is reported as
an error, never answered with simulated values.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from ..commander import protocol as p
from ..commander.device import CommanderPro, LoggingTransport
from ..commander.hidraw import (
    DeviceBusyError,
    DeviceEntry,
    HidrawTransport,
    PermissionDeniedError,
    TransportError,
    find_devices,
    kernel_driver,
)
from ..commander.protocol import DeviceStatusError, FanMode, ProtocolError
from .simulator import SimulatedCommander

log = logging.getLogger("comcontrol.hardware")

RAILS = ("12v", "5v", "3v3")


class ConnectError(Exception):
    """The controller could not be opened; `code` is a stable API error code."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class ConnectionLost(Exception):
    """Transport failed during an operation; the adapter is now disconnected."""


class ClosableTransport(Protocol):
    def exchange(self, frame: bytes) -> bytes: ...
    def close(self) -> None: ...


class Connector(Protocol):
    simulated: bool

    @property
    def usb_path(self) -> str | None: ...
    def open(self) -> ClosableTransport: ...


class HidrawConnector:
    """Opens only `1b1c:0c10` at exactly the configured USB port path."""

    simulated = False

    def __init__(self, usb_path: str | None, timeout: float = 1.0) -> None:
        self._usb_path = usb_path
        self.timeout = timeout

    @property
    def usb_path(self) -> str | None:
        return self._usb_path

    def open(self) -> ClosableTransport:
        found = find_devices()
        if not self._usb_path:
            raise ConnectError(
                "device_not_assigned",
                "no USB port path assigned",
                candidates=[d.usb_path for d in found],
            )
        matches = [d for d in found if d.usb_path == self._usb_path]
        if not matches:
            raise ConnectError(
                "device_not_found",
                f"no Commander PRO at USB path {self._usb_path}",
                usb_path=self._usb_path,
                candidates=[d.usb_path for d in found],
            )
        entry: DeviceEntry = matches[0]
        driver = kernel_driver(entry)
        if driver == "corsair-cpro":
            raise ConnectError(
                "kernel_driver_conflict",
                "kernel module corsair-cpro is bound to the device",
                driver=driver,
            )
        try:
            return HidrawTransport(entry, timeout=self.timeout)
        except PermissionDeniedError as exc:
            raise ConnectError("permission_denied", str(exc), node=entry.node) from exc
        except DeviceBusyError as exc:
            raise ConnectError("device_busy", str(exc), node=entry.node) from exc
        except TransportError as exc:
            raise ConnectError("device_open_failed", str(exc), node=entry.node) from exc


class SimulatorConnector:
    simulated = True

    def __init__(self, sim: SimulatedCommander) -> None:
        self.sim = sim

    @property
    def usb_path(self) -> str | None:
        return "simulated"

    def open(self) -> ClosableTransport:
        if self.sim.faults.unplugged:
            raise ConnectError("device_not_found", "simulated controller unplugged")
        return self.sim


# --- results -------------------------------------------------------------

@dataclass(frozen=True)
class DeviceInfo:
    firmware: str
    bootloader: str
    usb_path: str | None


@dataclass
class ValueRead:
    """One value of one poll: either a value or an error code, never both."""

    value: float | int | None = None
    error: str | None = None


@dataclass
class PollResult:
    sensors_connected: list[bool]
    fan_modes: list[str]          # disconnected | dc | pwm | unknown
    fan_modes_raw: list[int]
    temperatures: list[ValueRead | None]  # None = sensor not connected, not read
    rpms: list[ValueRead | None]          # None = no fan detected, not read
    rails: list[ValueRead]


@dataclass
class CommandOutcome:
    """Result of a sequence of write commands to one fan."""

    steps: list[dict] = field(default_factory=list)  # {"command", "ok", "error"}
    ok: bool = True
    # True when at least one command may have changed the hardware although
    # the sequence as a whole failed (partially applied, or a write timed out).
    uncertain: bool = False
    error: str | None = None


def _mode_name(raw: int) -> str:
    try:
        return {FanMode.DISCONNECTED: "disconnected", FanMode.DC: "dc", FanMode.PWM: "pwm"}[
            FanMode(raw)
        ]
    except ValueError:
        return "unknown"


def _error_code(exc: Exception) -> str:
    if isinstance(exc, DeviceStatusError):
        return f"device_status_{exc.status:02x}"
    if isinstance(exc, ProtocolError):
        return "invalid_response"
    return "error"


class HardwareAdapter:
    """Owns the controller connection.  Not thread-safe by design."""

    def __init__(self, connector: Connector, *, raw_log: bool = False) -> None:
        self.connector = connector
        self.raw_log = raw_log
        self._transport: ClosableTransport | None = None
        self._dev: CommanderPro | None = None
        self.info: DeviceInfo | None = None

    @property
    def simulated(self) -> bool:
        return self.connector.simulated

    @property
    def connected(self) -> bool:
        return self._dev is not None

    def connect(self) -> DeviceInfo:
        """Open and initialise; reads firmware and current population."""
        self.disconnect()
        transport = self.connector.open()
        self._transport = transport
        self._dev = CommanderPro(LoggingTransport(transport) if self.raw_log else transport)
        try:
            fw = self._dev.firmware()
            bl = self._dev.bootloader()
        except TransportError as exc:
            self.disconnect()
            raise ConnectError("device_io_error", str(exc)) from exc
        except ProtocolError as exc:
            self.disconnect()
            raise ConnectError("invalid_response", str(exc)) from exc
        self.info = DeviceInfo(".".join(map(str, fw)), ".".join(map(str, bl)), self.connector.usb_path)
        log.info("controller initialised at %s, firmware %s", self.info.usb_path, self.info.firmware)
        return self.info

    def disconnect(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # pragma: no cover - best effort
                log.exception("closing transport failed")
        self._transport = None
        self._dev = None

    def _require(self) -> CommanderPro:
        if self._dev is None:
            raise ConnectionLost("not connected")
        return self._dev

    def _lost(self, exc: Exception) -> ConnectionLost:
        log.warning("connection lost: %s", exc)
        self.disconnect()
        return ConnectionLost(str(exc))

    def poll(self) -> PollResult:
        """Read population, temperatures, fan speeds and rails.

        Per-value device/protocol errors are reported per value.  A failing
        population read or any transport error aborts the poll.
        """
        dev = self._require()
        try:
            connected = dev.temp_connected()
            modes = dev.fan_modes()
        except TransportError as exc:
            raise self._lost(exc) from exc
        raw_modes = [int(m) for m in modes]
        names = [_mode_name(m) for m in raw_modes]

        def read(fn, *args) -> ValueRead:
            try:
                return ValueRead(value=fn(*args))
            except TransportError as exc:
                raise self._lost(exc) from exc
            except (ProtocolError, ValueError) as exc:
                return ValueRead(error=_error_code(exc))

        temps = [read(dev.temperature, i) if ok else None for i, ok in enumerate(connected)]
        rpms = [read(dev.fan_rpm, i) if n in ("dc", "pwm") else None for i, n in enumerate(names)]
        rails = [read(dev.voltage, i) for i in range(len(RAILS))]
        return PollResult(connected, names, raw_modes, temps, rpms, rails)

    # --- writes -----------------------------------------------------------
    def apply_fan(
        self,
        fan: int,
        *,
        mode: str | None,
        percent: int | None = None,
        curve: tuple[int, Sequence[tuple[float, int]]] | None = None,
    ) -> CommandOutcome:
        """Optionally set the connector mode, then fixed power or a curve.

        Returns what was sent.  The controller confirms only receipt; a
        successful outcome is "transmitted", not "verified".
        """
        dev = self._require()
        if (percent is None) == (curve is None):
            raise ValueError("exactly one of percent or curve is required")
        plan: list[tuple[str, object]] = []
        if mode is not None:
            plan.append(("set_mode", lambda: dev.set_fan_mode(fan, FanMode[mode.upper()])))
        if percent is not None:
            plan.append(("set_percent", lambda: dev.set_fixed_percent(fan, percent)))
        else:
            sensor, points = curve  # type: ignore[misc]
            plan.append(("set_curve", lambda: dev.set_curve(fan, sensor, points)))

        outcome = CommandOutcome()
        for name, call in plan:
            try:
                call()  # type: ignore[operator]
                outcome.steps.append({"command": name, "ok": True, "error": None})
            except TransportError as exc:
                # A write may or may not have reached the controller.
                outcome.steps.append({"command": name, "ok": False, "error": "device_io_error"})
                outcome.ok, outcome.uncertain, outcome.error = False, True, "device_io_error"
                self._lost(exc)
                return outcome
            except (ProtocolError, ValueError) as exc:
                code = _error_code(exc)
                outcome.steps.append({"command": name, "ok": False, "error": code})
                outcome.ok, outcome.error = False, code
                # an earlier step succeeded, or the answer was unreadable
                outcome.uncertain = any(s["ok"] for s in outcome.steps) or code == "invalid_response"
                return outcome
        return outcome
