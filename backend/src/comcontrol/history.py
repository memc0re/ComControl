"""History queries over per-minute aggregates, condensed server-side.

Long ranges are reduced to at most `max_points` buckets per source.  Each
bucket keeps the count-weighted mean and the true minimum and maximum, so
peaks are never averaged away.  Minutes without valid readings produce no
bucket (a gap), never a zero value.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from .db import Database, iso

MAX_RANGE = timedelta(days=366)
MAX_POINTS = 2000
SOURCES = (
    *(f"temp{i}" for i in range(1, 5)),
    *(f"fan{i}" for i in range(1, 7)),
    "rail_12v", "rail_5v", "rail_3v3",
)
UNITS = {"temp": "°C", "fan": "rpm", "rail": "V"}


class HistoryError(ValueError):
    def __init__(self, code: str, **details: object) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def unit(source: str) -> str:
    return next(u for prefix, u in UNITS.items() if source.startswith(prefix))


def query(db: Database, sources: list[str], start: datetime, end: datetime, max_points: int) -> dict:
    if not sources or any(s not in SOURCES for s in sources) or len(set(sources)) != len(sources):
        raise HistoryError("history_invalid_source", allowed=list(SOURCES))
    if start.tzinfo is None or end.tzinfo is None:
        raise HistoryError("history_timezone_required")
    if end <= start:
        raise HistoryError("history_invalid_range")
    if end - start > MAX_RANGE:
        raise HistoryError("history_range_too_long", max_days=MAX_RANGE.days)
    if not 1 <= max_points <= MAX_POINTS:
        raise HistoryError("history_too_many_points", max=MAX_POINTS)
    t0 = int(start.timestamp()) // 60 * 60
    t1 = int(end.timestamp())
    minutes = math.ceil((t1 - t0) / 60)
    bucket = max(1, math.ceil(minutes / max_points)) * 60
    placeholders = ",".join("?" * len(sources))
    rows = db.query(
        f"SELECT source, (minute - ?) / ? AS b, SUM(avg * count) / SUM(count) AS avg,"
        f" MIN(min) AS min, MAX(max) AS max, SUM(count) AS count"
        f" FROM measurements_minute WHERE source IN ({placeholders}) AND minute >= ? AND minute < ?"
        f" GROUP BY source, b ORDER BY source, b",
        (t0, bucket, *sources, t0, t1),
    )
    series = {s: [] for s in sources}
    for r in rows:
        series[r["source"]].append({
            "t": iso(datetime.fromtimestamp(t0 + r["b"] * bucket, UTC)),
            "avg": round(r["avg"], 3), "min": r["min"], "max": r["max"], "count": r["count"],
        })
    return {
        "from": iso(datetime.fromtimestamp(t0, UTC)),
        "to": iso(datetime.fromtimestamp(t1, UTC)),
        "bucket_s": bucket,
        "series": [{"source": s, "unit": unit(s), "points": series[s]} for s in sources],
    }
