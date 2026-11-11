"""Channel configuration with a global configuration revision.

Every change must name the revision it was based on; a stale revision is a
conflict (HTTP 409).  Detected hardware state is kept elsewhere: this module
holds only what the administrator *expects* and *wants*.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .db import Database, iso, utc_now

NUM_FANS = 6
NUM_SENSORS = 4


class RevisionConflict(Exception):
    def __init__(self, current: int) -> None:
        super().__init__(f"configuration revision is {current}")
        self.current = current


@dataclass
class FanConfig:
    port: int
    name: str
    usage: str                  # unused | expected
    max_rpm: int | None
    dc_start_percent: int
    fan_mode: str               # auto | dc | pwm
    control_type: str           # fixed | curve
    percent: int
    curve_sensor: int | None    # 1-based
    curve_points: list[tuple[float, int]] | None

    @property
    def id(self) -> str:
        return f"fan{self.port}"


@dataclass
class SensorConfig:
    port: int
    name: str
    usage: str
    alarm_max_c: float | None

    @property
    def id(self) -> str:
        return f"temp{self.port}"


def _fan(row: sqlite3.Row) -> FanConfig:
    pts = json.loads(row["curve_points"]) if row["curve_points"] else None
    return FanConfig(
        port=row["port"],
        name=row["name"],
        usage=row["usage"],
        max_rpm=row["max_rpm"],
        dc_start_percent=row["dc_start_percent"],
        fan_mode=row["fan_mode"],
        control_type=row["control_type"],
        percent=row["percent"],
        curve_sensor=row["curve_sensor"],
        curve_points=[(float(t), int(r)) for t, r in pts] if pts is not None else None,
    )


def _sensor(row: sqlite3.Row) -> SensorConfig:
    return SensorConfig(row["port"], row["name"], row["usage"], row["alarm_max_c"])


class ConfigStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def revision(self) -> int:
        row = self.db.one("SELECT value FROM meta WHERE key = 'config_revision'")
        return int(row["value"]) if row else 0

    def fans(self) -> list[FanConfig]:
        return [_fan(r) for r in self.db.query("SELECT * FROM fans ORDER BY port")]

    def fan(self, port: int) -> FanConfig:
        row = self.db.one("SELECT * FROM fans WHERE port = ?", (port,))
        if row is None:
            raise KeyError(port)
        return _fan(row)

    def sensors(self) -> list[SensorConfig]:
        return [_sensor(r) for r in self.db.query("SELECT * FROM sensors ORDER BY port")]

    def sensor(self, port: int) -> SensorConfig:
        row = self.db.one("SELECT * FROM sensors WHERE port = ?", (port,))
        if row is None:
            raise KeyError(port)
        return _sensor(row)

    def device_usb_path(self) -> str | None:
        row = self.db.one("SELECT value FROM meta WHERE key = 'device_usb_path'")
        return row["value"] if row else None

    # --- writes (all bump the revision) -----------------------------------
    def _bump(self, c: sqlite3.Connection, expected_revision: int) -> int:
        current = int(c.execute("SELECT value FROM meta WHERE key = 'config_revision'").fetchone()[0])
        if current != expected_revision:
            raise RevisionConflict(current)
        c.execute("UPDATE meta SET value = ? WHERE key = 'config_revision'", (str(current + 1),))
        return current + 1

    def save_fan(self, fan: FanConfig, expected_revision: int) -> int:
        with self.db.tx() as c:
            rev = self._bump(c, expected_revision)
            c.execute(
                "UPDATE fans SET name = ?, usage = ?, max_rpm = ?, dc_start_percent = ?, fan_mode = ?,"
                " control_type = ?, percent = ?, curve_sensor = ?, curve_points = ? WHERE port = ?",
                (
                    fan.name,
                    fan.usage,
                    fan.max_rpm,
                    fan.dc_start_percent,
                    fan.fan_mode,
                    fan.control_type,
                    fan.percent,
                    fan.curve_sensor,
                    json.dumps(fan.curve_points) if fan.curve_points is not None else None,
                    fan.port,
                ),
            )
        return rev

    def save_sensor(self, sensor: SensorConfig, expected_revision: int) -> int:
        with self.db.tx() as c:
            rev = self._bump(c, expected_revision)
            c.execute(
                "UPDATE sensors SET name = ?, usage = ?, alarm_max_c = ? WHERE port = ?",
                (sensor.name, sensor.usage, sensor.alarm_max_c, sensor.port),
            )
        return rev

    def set_device_usb_path(self, usb_path: str, expected_revision: int | None) -> int:
        with self.db.tx() as c:
            if expected_revision is None:  # initial seeding from the environment
                rev = int(c.execute("SELECT value FROM meta WHERE key = 'config_revision'").fetchone()[0])
            else:
                rev = self._bump(c, expected_revision)
            c.execute(
                "INSERT INTO meta (key, value) VALUES ('device_usb_path', ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (usb_path,),
            )
        return rev

    # --- last transmitted command per fan ---------------------------------
    def record_command(self, port: int, command: dict, ok: bool, uncertain: bool, error: str | None) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO fan_commands (port, sent_at, command, ok, uncertain, error)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (port) DO UPDATE SET sent_at = excluded.sent_at,"
                " command = excluded.command, ok = excluded.ok, uncertain = excluded.uncertain,"
                " error = excluded.error",
                (port, iso(utc_now()), json.dumps(command, sort_keys=True), int(ok), int(uncertain), error),
            )

    def last_commands(self) -> dict[int, dict]:
        out = {}
        for r in self.db.query("SELECT * FROM fan_commands"):
            out[r["port"]] = {
                "sent_at": r["sent_at"],
                "command": json.loads(r["command"]),
                "ok": bool(r["ok"]),
                "uncertain": bool(r["uncertain"]),
                "error": r["error"],
            }
        return out
