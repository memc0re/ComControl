"""Alarms and the latched fail-safe reaction (100 % fixed power).

Alarm types (one active record per type and source, no duplicates on
repeated detection; acknowledging does not end an alarm):

* `over_temperature`  - expected sensor above its limit (ends 1 K below it)
* `sensor_failure`    - expected sensor missing, unreadable or stale
* `fan_stall`         - expected fan with a running request reports three
                        consecutive valid 0 RPM readings
* `fan_missing`       - expected fan not reported by the controller
* `device_error`      - controller not reachable / communication failure

Fail-safe: fans affected by an over-temperature (curves using that sensor,
or all expected fans if no curve uses it), by a failed curve sensor (fans
whose curve uses it), by a stalled fan (all expected fans) or by a reconnect
(all expected fans) are latched at 100 % fixed power.  The latch is stored
in the database, survives restarts and is only released by an explicit
administrator action once its cause has ended.  If the 100 % command cannot
be delivered the latch says so ("not deliverable") and delivery is retried.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .curves import CurveError, effective_curve
from .db import iso, utc_now

if TYPE_CHECKING:
    from .runtime import Runtime

log = logging.getLogger("comcontrol.safety")

STALL_COUNT = 3
OVER_TEMP_HYSTERESIS = 1.0
CAUSE_TYPES = ("over_temperature", "sensor_failure", "fan_stall", "device_error")


@dataclass
class Latch:
    port: int
    reason: str
    source: str
    since: str
    delivered: bool
    last_error: str | None


def interpolate(points: list[tuple[float, int]], temp: float) -> float:
    if temp <= points[0][0]:
        return points[0][1]
    for (t0, r0), (t1, r1) in zip(points, points[1:]):
        if temp <= t1:
            return r0 + (r1 - r0) * (temp - t0) / (t1 - t0)
    return points[-1][1]


class Safety:
    def __init__(self, rt: "Runtime") -> None:
        self.rt = rt
        self.db = rt.db
        self._zero_counts: dict[int, int] = {}
        self._request_sig: dict[int, object] = {}
        self._counted_at: dict[int, float | None] = {}  # valid_mono of the last counted reading

    # --- persistence helpers -------------------------------------------------
    def latches(self) -> dict[int, Latch]:
        return {
            r["port"]: Latch(r["port"], r["reason"], r["source"], r["since"], bool(r["delivered"]), r["last_error"])
            for r in self.db.query("SELECT * FROM failsafe")
        }

    def active_alarms(self) -> dict[tuple[str, str], dict]:
        rows = self.db.query("SELECT * FROM alarms WHERE ended_at IS NULL")
        return {(r["type"], r["source"]): dict(r) for r in rows}

    def _raise(self, type_: str, source: str, active: dict, **details: object) -> bool:
        if (type_, source) in active:
            return False
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO alarms (type, source, started_at, details) VALUES (?, ?, ?, ?)",
                (type_, source, iso(utc_now()), json.dumps(details, sort_keys=True)),
            )
        active[(type_, source)] = {"type": type_, "source": source}
        self.db.add_event("alarm_raised", type=type_, source=source, **details)
        log.warning("alarm %s on %s %s", type_, source, details or "")
        return True

    def _clear(self, type_: str, source: str, active: dict) -> None:
        if (type_, source) not in active:
            return
        with self.db.tx() as c:
            c.execute(
                "UPDATE alarms SET ended_at = ? WHERE type = ? AND source = ? AND ended_at IS NULL",
                (iso(utc_now()), type_, source),
            )
        del active[(type_, source)]
        self.db.add_event("alarm_ended", type=type_, source=source)
        log.info("alarm %s on %s ended", type_, source)

    def acknowledge(self, alarm_id: int) -> bool:
        with self.db.tx() as c:
            n = c.execute(
                "UPDATE alarms SET acknowledged_at = ? WHERE id = ? AND acknowledged_at IS NULL",
                (iso(utc_now()), alarm_id),
            ).rowcount
            exists = c.execute("SELECT 1 FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
        if n:
            self.db.add_event("alarm_acknowledged", id=alarm_id)
        return exists is not None

    # --- evaluation ------------------------------------------------------------
    def _requested(self, fan, latched: bool, temp: dict[int, float | None]) -> tuple[object, bool]:
        """(request signature, running expected) for stall detection."""
        rt = self.rt
        if latched:
            return ("fixed", 100), True
        if fan.control_type == "fixed":
            sig = ("fixed", fan.percent)
            if fan.percent <= 0:
                return sig, False
            if rt.live.fan_modes[fan.port - 1] == "dc" and fan.percent < fan.dc_start_percent:
                return sig, False  # DC fans may stand still below their start threshold
            return sig, True
        try:
            eff = effective_curve(fan.curve_points or [], max_rpm=fan.max_rpm)
        except CurveError:
            return ("curve", None), False
        t = temp.get(fan.curve_sensor or 0)
        if t is None:
            return ("curve", None), False
        target = interpolate(eff, t)
        return ("curve", target > 0), target > 0

    def required_latches(self, active: dict, fans: list) -> dict[int, tuple[str, str]]:
        """Fans that must be at 100 % because of currently active causes."""
        expected = [f for f in fans if f.usage == "expected"]
        need: dict[int, tuple[str, str]] = {}
        for (type_, source) in active:
            if type_ == "over_temperature":
                port = int(source[4:])
                users = [f for f in expected if f.control_type == "curve" and f.curve_sensor == port]
                for f in users or expected:
                    need.setdefault(f.port, (type_, source))
            elif type_ == "sensor_failure":
                port = int(source[4:])
                for f in expected:
                    if f.control_type == "curve" and f.curve_sensor == port:
                        need.setdefault(f.port, (type_, source))
            elif type_ == "fan_stall":
                for f in expected:
                    need.setdefault(f.port, (type_, source))
        return need

    async def evaluate(self) -> None:
        """Run after every poll cycle (successful or not)."""
        rt = self.rt
        active = self.active_alarms()
        connected = rt.adapter.connected
        sensors = rt.store.sensors()
        fans = rt.store.fans()
        now = rt.mono()
        inv_age = rt.live.inventory_age(now)

        if connected:
            self._clear("device_error", "device", active)
        elif rt.connection.error_code:
            self._raise("device_error", "device", active, code=rt.connection.error_code)

        temps: dict[int, float | None] = {}
        for s in sensors:
            reading = rt.live.sensors[s.port - 1]
            status = reading.status(now, inv_age, rt.settings.stale_after)
            value = float(reading.value) if status == "ok" and reading.value is not None else None
            temps[s.port] = value
            if s.usage != "expected":
                self._clear("sensor_failure", s.id, active)
                self._clear("over_temperature", s.id, active)
                continue
            if connected:
                if status in ("not_connected", "error", "stale"):
                    self._raise("sensor_failure", s.id, active, status=status)
                elif status == "ok":
                    self._clear("sensor_failure", s.id, active)
            if s.alarm_max_c is None:
                self._clear("over_temperature", s.id, active)
            elif value is not None:
                if value > s.alarm_max_c:
                    self._raise("over_temperature", s.id, active, value=value, limit=s.alarm_max_c)
                elif value < s.alarm_max_c - OVER_TEMP_HYSTERESIS:
                    self._clear("over_temperature", s.id, active)

        latches = self.latches()
        for f in fans:
            reading = rt.live.fans[f.port - 1]
            if f.usage != "expected":
                self._clear("fan_stall", f.id, active)
                self._clear("fan_missing", f.id, active)
                self._zero_counts.pop(f.port, None)
                continue
            status = reading.status(now, inv_age, rt.settings.stale_after)
            if connected and status == "not_connected":
                self._raise("fan_missing", f.id, active)
            elif status == "ok":
                self._clear("fan_missing", f.id, active)
            sig, running = self._requested(f, f.port in latches, temps)
            if self._request_sig.get(f.port) != sig:
                self._request_sig[f.port] = sig
                self._zero_counts[f.port] = 0
            if status != "ok" or self._counted_at.get(f.port) == reading.valid_mono:
                continue  # missing (or already evaluated) values neither count nor reset
            self._counted_at[f.port] = reading.valid_mono
            if reading.value and reading.value > 0:
                self._zero_counts[f.port] = 0
                self._clear("fan_stall", f.id, active)
            elif running:
                self._zero_counts[f.port] = self._zero_counts.get(f.port, 0) + 1
                if self._zero_counts[f.port] >= STALL_COUNT:
                    self._raise("fan_stall", f.id, active, consecutive_zero=self._zero_counts[f.port])

        need = self.required_latches(active, fans)
        new = {p: c for p, c in need.items() if p not in latches}
        if new:
            await self.latch(new)
        await self.retry_undelivered()

    # --- latching and delivery ---------------------------------------------------
    async def latch(self, ports: dict[int, tuple[str, str]]) -> None:
        inserted = []
        with self.db.tx() as c:
            for port, (reason, source) in ports.items():
                if c.execute(
                    "INSERT INTO failsafe (port, reason, source, since, delivered) VALUES (?, ?, ?, ?, 0)"
                    " ON CONFLICT (port) DO NOTHING",
                    (port, reason, source, iso(utc_now())),
                ).rowcount:
                    inserted.append(port)
        for port in inserted:
            reason, source = ports[port]
            self.db.add_event("failsafe_latched", fan=f"fan{port}", reason=reason, source=source)
            log.warning("fail-safe: fan %d latched at 100 %% (%s on %s)", port, reason, source)
        await self.deliver(list(ports))

    async def deliver(self, ports: list[int]) -> None:
        rt = self.rt
        for port in ports:
            if rt.adapter.connected and rt.live.fan_modes[port - 1] in ("dc", "pwm", None):
                outcome = await rt._send(port, mode=None, percent=100, reason="failsafe")
                ok, err = outcome.ok, outcome.error
            else:
                ok, err = False, "device_unavailable" if not rt.adapter.connected else "fan_not_detected"
            with self.db.tx() as c:
                c.execute("UPDATE failsafe SET delivered = ?, last_error = ? WHERE port = ?",
                          (int(ok), err, port))
            if not ok:
                log.error("fail-safe for fan %d not deliverable (%s)", port, err)

    async def retry_undelivered(self) -> None:
        pending = [p for p, latch in self.latches().items() if not latch.delivered]
        if pending and self.rt.adapter.connected:
            await self.deliver(pending)

    def mark_undelivered(self) -> None:
        """After a connection loss nothing on the controller is guaranteed any more."""
        with self.db.tx() as c:
            c.execute("UPDATE failsafe SET delivered = 0, last_error = 'device_unavailable'")

    async def release(self) -> tuple[list[int], list[int], list[int]]:
        """Admin release: returns (released, blocked by active cause, failed to re-apply)."""
        rt = self.rt
        active = self.active_alarms()
        need = self.required_latches(active, rt.store.fans())
        device_down = ("device_error", "device") in active or not rt.adapter.connected
        released, blocked, failed = [], [], []
        for port in sorted(self.latches()):
            if port in need or device_down:
                blocked.append(port)
                continue
            fan = rt.store.fan(port)
            if fan.usage == "expected":
                try:
                    outcome, _ = await rt._send_config(fan, "failsafe_release")
                    ok = outcome.ok
                except Exception as exc:  # ControlError: stored profile not applicable now
                    log.warning("fan %d: profile not re-applied on release (%s)", port, exc)
                    ok = False
                if not ok:
                    failed.append(port)
                    continue
            with self.db.tx() as c:
                c.execute("DELETE FROM failsafe WHERE port = ?", (port,))
            released.append(port)
        if released:
            self.db.add_event("failsafe_released", fans=[f"fan{p}" for p in released])
            log.info("fail-safe released for fans %s", released)
        return released, blocked, failed

    def drop_latch(self, port: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM failsafe WHERE port = ?", (port,))
