"""Runtime: owns the hardware adapter, polls every 5 s and applies settings.

The poll loop and all control actions share one hardware worker thread, so
device commands are strictly serialised.  Configuration changes are
serialised by `config_lock`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from .config_store import ConfigStore, FanConfig, RevisionConflict, SensorConfig
from .curves import CurveError, effective_curve
from .db import Database, utc_now
from .hardware.adapter import (
    CommandOutcome,
    ConnectError,
    ConnectionLost,
    HardwareAdapter,
    HidrawConnector,
)
from .hardware.worker import HardwareTimeout, HardwareWorker
from .live import LiveState, MinuteAggregator, purge_history, source_values
from .safety import Safety
from .settings import Settings

log = logging.getLogger("comcontrol.runtime")


class ControlError(Exception):
    """A rejected control/config request; `code` is the API error code."""

    def __init__(self, code: str, status: int = 422, **details: object) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.details = details


@dataclass
class Connection:
    state: str = "disconnected"  # connected | disconnected
    error_code: str | None = None
    error_message: str | None = None
    error_details: dict = field(default_factory=dict)
    since: datetime | None = None
    firmware: str | None = None
    bootloader: str | None = None
    usb_path: str | None = None
    connects: int = 0  # successful connections in this process


@dataclass
class ApplyResult:
    revision: int
    config: FanConfig
    outcome: CommandOutcome | None
    effective_curve: list[tuple[float, int]] | None = None


class Runtime:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        adapter: HardwareAdapter,
        *,
        mono: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = utc_now,
    ) -> None:
        self.settings = settings
        self.db = db
        self.store = ConfigStore(db)
        self.adapter = adapter
        self.worker = HardwareWorker()
        self.live = LiveState()
        self.aggregator = MinuteAggregator(db)
        self.connection = Connection(usb_path=adapter.connector.usb_path)
        self.config_lock = asyncio.Lock()
        self.mono = mono
        self.wall = wall
        self.safety = Safety(self)
        self._task: asyncio.Task | None = None
        self._last_purge = 0.0

    @property
    def simulated(self) -> bool:
        return self.adapter.simulated

    # --- lifecycle ----------------------------------------------------------
    async def start(self, *, background: bool = True) -> None:
        self.db.add_event("backend_started", simulated=self.simulated)
        log.info("backend started (%s)", "SIMULATION" if self.simulated else "hardware")
        if background:
            self._task = asyncio.create_task(self._loop(), name="comcontrol-poll")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        try:
            await self.worker.run(self.adapter.disconnect, timeout=self.settings.hardware_timeout)
        except HardwareTimeout:
            log.warning("disconnect timed out")
        self.worker.shutdown()
        self.aggregator.flush()

    async def _loop(self) -> None:
        interval = self.settings.poll_interval
        next_tick = self.mono()
        while True:
            try:
                await self.tick()
            except Exception:  # never let the loop die
                log.exception("poll cycle failed")
            next_tick += interval
            delay = next_tick - self.mono()
            if delay < 0:  # fell behind: skip missed ticks instead of bursting
                next_tick = self.mono()
                delay = 0
            await asyncio.sleep(delay)

    # --- one poll cycle -----------------------------------------------------
    async def tick(self) -> None:
        timeout = self.settings.hardware_timeout
        if not self.adapter.connected:
            if not await self._connect():
                await self.safety.evaluate()
                return
            first = self.connection.connects == 1
            if not first:
                # After a reconnect: request 100 % first (latched), then check.
                expected = {f.port: ("reconnect", "device") for f in self.store.fans() if f.usage == "expected"}
                await self.safety.latch(expected)
                await self.safety.retry_undelivered()
            if not await self._poll(timeout):
                await self.safety.evaluate()
                return
            if first:
                await self._reapply_stored()
            await self.safety.evaluate()
            return
        await self._poll(timeout)
        await self.safety.evaluate()
        now = time.time()
        if now - self._last_purge > 3600:
            self._last_purge = now
            purge_history(self.db, self.wall(), self.settings.retention_days)

    async def _connect(self) -> bool:
        try:
            info = await self.worker.run(self.adapter.connect, timeout=self.settings.hardware_timeout)
        except ConnectError as exc:
            self._set_disconnected(exc.code, str(exc), exc.details)
            return False
        except HardwareTimeout as exc:
            self._set_disconnected("hardware_timeout", str(exc), {})
            return False
        c = self.connection
        c.state, c.error_code, c.error_message, c.error_details = "connected", None, None, {}
        c.since, c.firmware, c.bootloader, c.usb_path = self.wall(), info.firmware, info.bootloader, info.usb_path
        c.connects += 1
        self.db.add_event("device_connected", usb_path=info.usb_path, firmware=info.firmware)
        return True

    def _set_disconnected(self, code: str, message: str, details: dict) -> None:
        c = self.connection
        changed = c.state != "disconnected" or c.error_code != code
        if c.state == "connected":
            self.db.add_event("device_disconnected", error=code)
            log.warning("controller connection lost: %s", message)
            self.safety.mark_undelivered()
        elif changed:
            log.warning("controller not available: %s (%s)", message, code)
        c.state, c.error_code, c.error_message, c.error_details = "disconnected", code, message, details
        if changed:
            c.since = self.wall()

    async def _poll(self, timeout: float) -> bool:
        try:
            poll = await self.worker.run(self.adapter.poll, timeout=timeout)
        except ConnectionLost as exc:
            self._set_disconnected("device_io_error", str(exc), {})
            return False
        except HardwareTimeout as exc:
            await self.worker.run(self.adapter.disconnect, timeout=timeout)
            self._set_disconnected("hardware_timeout", str(exc), {})
            return False
        mono, wall = self.mono(), self.wall()
        self.live.apply_poll(poll, mono, wall)
        self.aggregator.add(wall, source_values(poll))
        return True

    # --- sending -----------------------------------------------------------
    async def _send(self, port: int, *, mode: str | None, percent: int | None = None,
                    curve: tuple[int, list[tuple[float, int]]] | None = None,
                    reason: str) -> CommandOutcome:
        command: dict = {"reason": reason, "mode": mode}
        if percent is not None:
            command.update(type="fixed", percent=percent)
        else:
            assert curve is not None
            command.update(type="curve", sensor=curve[0] + 1, points=[list(p) for p in curve[1]])
        try:
            outcome = await self.worker.run(
                lambda: self.adapter.apply_fan(port - 1, mode=mode, percent=percent, curve=curve),
                timeout=self.settings.hardware_timeout,
            )
        except ConnectionLost:
            outcome = CommandOutcome(ok=False, error="device_unavailable")
        except HardwareTimeout:
            outcome = CommandOutcome(ok=False, uncertain=True, error="hardware_timeout")
        if not self.adapter.connected and self.connection.state == "connected":
            self._set_disconnected("device_io_error", "connection lost while sending", {})
        self.store.record_command(port, command, outcome.ok, outcome.uncertain, outcome.error)
        if outcome.ok:
            log.info("fan %d: sent %s (%s)", port, command.get("type"), reason)
        else:
            log.warning("fan %d: sending failed (%s, uncertain=%s)", port, outcome.error, outcome.uncertain)
        return outcome

    def _curve_for(self, fan: FanConfig) -> list[tuple[float, int]]:
        if fan.curve_sensor is None or fan.curve_points is None:
            raise ControlError("curve_missing")
        sensor = self.store.sensor(fan.curve_sensor)
        if sensor.usage != "expected":
            raise ControlError("curve_sensor_not_expected", sensor=sensor.id)
        reading = self.live.sensors[fan.curve_sensor - 1]
        status = reading.status(self.mono(), self.live.inventory_age(self.mono()), self.settings.stale_after)
        if status != "ok":
            raise ControlError("curve_sensor_unavailable", sensor=sensor.id, sensor_status=status)
        try:
            return effective_curve(fan.curve_points, max_rpm=fan.max_rpm)
        except CurveError as exc:
            raise ControlError(exc.code, **exc.details) from exc

    async def _send_config(self, fan: FanConfig, reason: str) -> tuple[CommandOutcome, list | None]:
        mode = None if fan.fan_mode == "auto" else fan.fan_mode
        if fan.control_type == "fixed":
            return await self._send(fan.port, mode=mode, percent=fan.percent, reason=reason), None
        eff = self._curve_for(fan)
        outcome = await self._send(
            fan.port, mode=mode, curve=(fan.curve_sensor - 1, eff), reason=reason  # type: ignore[operator]
        )
        return outcome, eff

    async def _reapply_stored(self) -> None:
        """Regular start: re-apply valid stored settings after checking population."""
        async with self.config_lock:
            latched = self.safety.latches()
            if latched:
                # A latched fail-safe survives restarts: 100 % instead of the profile.
                await self.safety.deliver(sorted(latched))
            for fan in self.store.fans():
                if fan.usage != "expected" or fan.port in latched:
                    continue
                if not self._fan_detected(fan.port):
                    log.warning("fan %d expected but not detected; settings not re-applied", fan.port)
                    continue
                try:
                    await self._send_config(fan, "startup")
                except ControlError as exc:
                    log.warning("fan %d: stored settings not re-applied (%s)", fan.port, exc.code)
                    self.store.record_command(fan.port, {"reason": "startup"}, False, False, exc.code)

    def _fan_detected(self, port: int) -> bool:
        return self.live.fan_modes[port - 1] in ("dc", "pwm")

    def _require_device(self) -> None:
        if not self.adapter.connected:
            raise ControlError("device_unavailable", status=503, connection_error=self.connection.error_code)

    # --- configuration API ------------------------------------------------
    async def apply_fan_control(
        self,
        port: int,
        revision: int,
        *,
        fan_mode: str,
        control_type: str,
        percent: int | None,
        curve_sensor: int | None,
        curve_points: list[tuple[float, int]] | None,
    ) -> ApplyResult:
        """"Apply": validate, send, and store only after successful transmission."""
        async with self.config_lock:
            current = self.store.fan(port)
            if self.store.revision() != revision:
                raise RevisionConflict(self.store.revision())
            if current.usage != "expected":
                raise ControlError("fan_not_expected")
            if port in self.safety.latches():
                raise ControlError("failsafe_active", status=409)
            if control_type == "fixed":
                if percent is None:
                    raise ControlError("percent_missing")
                new = replace(current, fan_mode=fan_mode, control_type="fixed", percent=percent)
            else:
                new = replace(
                    current, fan_mode=fan_mode, control_type="curve",
                    curve_sensor=curve_sensor, curve_points=curve_points,
                )
                self._curve_for(new)  # validate before touching hardware
            self._require_device()
            if not self._fan_detected(port):
                raise ControlError("fan_not_detected")
            outcome, eff = await self._send_config(new, "apply")
            if not outcome.ok:
                # The last valid desired configuration stays untouched.
                raise ControlError(
                    "apply_failed", status=502, error=outcome.error,
                    uncertain=outcome.uncertain, steps=outcome.steps,
                )
            rev = self.store.save_fan(new, revision)
            self.db.add_event("fan_control_applied", fan=new.id, type=new.control_type)
            return ApplyResult(rev, new, outcome, eff)

    async def update_fan_settings(
        self, port: int, revision: int, *, name: str, usage: str, max_rpm: int | None,
        dc_start_percent: int,
    ) -> ApplyResult:
        async with self.config_lock:
            current = self.store.fan(port)
            new = replace(current, name=name, usage=usage, max_rpm=max_rpm, dc_start_percent=dc_start_percent)
            activating = current.usage == "unused" and usage == "expected"
            if activating:
                # A newly activated channel always starts at 100 % fixed power.
                new = replace(new, control_type="fixed", percent=100)
            elif new.usage == "expected" and new.control_type == "curve" and new.curve_points:
                try:
                    effective_curve(new.curve_points, max_rpm=new.max_rpm)
                except CurveError as exc:
                    raise ControlError("curve_conflicts_max_rpm", reason=exc.code) from exc
            rev = self.store.save_fan(new, revision)
            outcome = None
            if usage == "unused":
                self.safety.drop_latch(port)
            if activating:
                self.db.add_event("fan_activated", fan=new.id)
                if self.adapter.connected and self._fan_detected(port):
                    outcome = await self._send(port, mode=None, percent=100, reason="activation")
                else:
                    outcome = CommandOutcome(ok=False, error="device_unavailable"
                                             if not self.adapter.connected else "fan_not_detected")
            return ApplyResult(rev, new, outcome)

    async def update_sensor_settings(
        self, port: int, revision: int, *, name: str, usage: str, alarm_max_c: float | None
    ) -> tuple[int, SensorConfig]:
        async with self.config_lock:
            current = self.store.sensor(port)
            if usage == "unused":
                users = [f.id for f in self.store.fans()
                         if f.usage == "expected" and f.control_type == "curve" and f.curve_sensor == port]
                if users:
                    raise ControlError("sensor_in_use_by_curve", fans=users)
            new = replace(current, name=name, usage=usage, alarm_max_c=alarm_max_c)
            return self.store.save_sensor(new, revision), new

    async def release_failsafe(self) -> tuple[list[int], list[int], list[int]]:
        async with self.config_lock:
            return await self.safety.release()

    async def assign_device(self, usb_path: str, revision: int) -> int:
        """Explicit (re)assignment of the controller by USB port path."""
        if self.simulated:
            raise ControlError("simulation_active", status=409)
        async with self.config_lock:
            rev = self.store.set_device_usb_path(usb_path, revision)
            connector = self.adapter.connector
            assert isinstance(connector, HidrawConnector)
            await self.worker.run(self.adapter.disconnect, timeout=self.settings.hardware_timeout)
            connector._usb_path = usb_path
            self.connection = Connection(usb_path=usb_path, connects=max(1, self.connection.connects))
            self.live = LiveState()
            self.db.add_event("device_assigned", usb_path=usb_path)
            log.info("controller assigned to USB path %s", usb_path)
        return rev

    async def list_candidates(self) -> list[str]:
        if self.simulated:
            return ["simulated"]
        from .commander.hidraw import find_devices  # sysfs only, opens nothing

        return [d.usb_path for d in await self.worker.run(find_devices, timeout=self.settings.hardware_timeout)]
