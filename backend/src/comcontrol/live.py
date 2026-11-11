"""Cached live readings with validity/staleness, and per-minute aggregation.

Rules:
* Only valid readings get a timestamp.  A failed or incomplete poll never
  refreshes the timestamp of values it did not read successfully.
* Missing or invalid values are gaps (`None` + status), never 0 °C / 0 RPM.
  A measured 0 RPM of a detected fan is a valid value.
* Data age uses the monotonic clock; wall-clock times (UTC) are for display
  and history only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .db import Database
from .hardware.adapter import RAILS, PollResult, ValueRead


@dataclass
class Reading:
    value: float | int | None = None   # last valid value
    valid_mono: float | None = None
    valid_at: datetime | None = None
    error: str | None = None           # error of the latest attempt
    present: bool | None = None        # latest population read (None = unknown)

    def status(self, now_mono: float, inventory_age: float | None, stale_after: float) -> str:
        if inventory_age is None:
            return "no_data"
        if inventory_age > stale_after:
            return "stale"
        if self.present is False:
            return "not_connected"
        if self.error:
            return "error"
        if self.valid_mono is None:
            return "no_data"
        if now_mono - self.valid_mono > stale_after:
            return "stale"
        return "ok"

    def update(self, read: ValueRead | None, present: bool, mono: float, wall: datetime) -> None:
        self.present = present
        if not present:
            self.error = None
            self.value = None
            self.valid_mono = None
            self.valid_at = None
            return
        assert read is not None
        if read.error is not None:
            self.error = read.error
            return
        self.error = None
        self.value = read.value
        self.valid_mono = mono
        self.valid_at = wall


@dataclass
class LiveState:
    sensors: list[Reading] = field(default_factory=lambda: [Reading() for _ in range(4)])
    fans: list[Reading] = field(default_factory=lambda: [Reading() for _ in range(6)])
    rails: list[Reading] = field(default_factory=lambda: [Reading() for _ in RAILS])
    fan_modes: list[str | None] = field(default_factory=lambda: [None] * 6)
    fan_modes_raw: list[int | None] = field(default_factory=lambda: [None] * 6)
    inventory_mono: float | None = None
    inventory_at: datetime | None = None

    def apply_poll(self, poll: PollResult, mono: float, wall: datetime) -> None:
        self.inventory_mono, self.inventory_at = mono, wall
        for i, reading in enumerate(self.sensors):
            reading.update(poll.temperatures[i], poll.sensors_connected[i], mono, wall)
        for i, reading in enumerate(self.fans):
            present = poll.fan_modes[i] in ("dc", "pwm")
            reading.update(poll.rpms[i], present, mono, wall)
        for i, reading in enumerate(self.rails):
            reading.update(poll.rails[i], True, mono, wall)
        self.fan_modes = list(poll.fan_modes)
        self.fan_modes_raw = list(poll.fan_modes_raw)

    def inventory_age(self, now_mono: float) -> float | None:
        return None if self.inventory_mono is None else now_mono - self.inventory_mono


def source_values(poll: PollResult) -> dict[str, float]:
    """Valid values of one poll, keyed by history source id."""
    out: dict[str, float] = {}
    for i, r in enumerate(poll.temperatures):
        if r is not None and r.error is None and r.value is not None:
            out[f"temp{i + 1}"] = float(r.value)
    for i, r in enumerate(poll.rpms):
        if r is not None and r.error is None and r.value is not None:
            out[f"fan{i + 1}"] = float(r.value)
    for name, r in zip(RAILS, poll.rails):
        if r.error is None and r.value is not None:
            out[f"rail_{name}"] = float(r.value)
    return out


class MinuteAggregator:
    """Accumulates valid samples per UTC minute; flushes finished minutes."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.minute: int | None = None
        self._acc: dict[str, list[float]] = {}  # source -> [sum, min, max, count]

    def add(self, wall: datetime, values: dict[str, float]) -> None:
        minute = int(wall.astimezone(UTC).timestamp()) // 60 * 60
        if self.minute is not None and minute != self.minute:
            self.flush()
        self.minute = minute
        for source, v in values.items():
            acc = self._acc.get(source)
            if acc is None:
                self._acc[source] = [v, v, v, 1]
            else:
                acc[0] += v
                acc[1] = min(acc[1], v)
                acc[2] = max(acc[2], v)
                acc[3] += 1

    def flush(self) -> None:
        if self.minute is None or not self._acc:
            self._acc = {}
            return
        rows = [
            (self.minute, source, s / n, lo, hi, int(n)) for source, (s, lo, hi, n) in self._acc.items()
        ]
        with self.db.tx() as c:
            # A restart within the same minute merges into the existing row.
            c.executemany(
                "INSERT INTO measurements_minute (minute, source, avg, min, max, count)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (source, minute) DO UPDATE SET"
                " avg = (avg * count + excluded.avg * excluded.count) / (count + excluded.count),"
                " min = MIN(min, excluded.min), max = MAX(max, excluded.max),"
                " count = count + excluded.count",
                rows,
            )
        self._acc = {}


def purge_history(db: Database, now: datetime, retention_days: int) -> int:
    """Delete aggregates and events older than the retention period."""
    cutoff = int(now.timestamp()) - retention_days * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with db.tx() as c:
        n = c.execute("DELETE FROM measurements_minute WHERE minute < ?", (cutoff,)).rowcount
        n += c.execute("DELETE FROM events WHERE at < ?", (cutoff_iso,)).rowcount
    return n
