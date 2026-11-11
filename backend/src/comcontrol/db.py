"""SQLite persistence with versioned migrations.

One connection shared by the process, guarded by a lock; every change runs in
an explicit transaction (`with db.tx() as c:`).  The schema version is kept in
`PRAGMA user_version`; migrations are applied in order and never edited once
released - schema changes get a new migration.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE admin (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_default_password INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            csrf_token TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE TABLE fans (
            port INTEGER PRIMARY KEY CHECK (port BETWEEN 1 AND 6),
            name TEXT NOT NULL DEFAULT '',
            usage TEXT NOT NULL DEFAULT 'unused' CHECK (usage IN ('unused', 'expected')),
            max_rpm INTEGER,
            dc_start_percent INTEGER NOT NULL DEFAULT 50,
            fan_mode TEXT NOT NULL DEFAULT 'auto' CHECK (fan_mode IN ('auto', 'dc', 'pwm')),
            control_type TEXT NOT NULL DEFAULT 'fixed' CHECK (control_type IN ('fixed', 'curve')),
            percent INTEGER NOT NULL DEFAULT 100,
            curve_sensor INTEGER,
            curve_points TEXT
        );
        CREATE TABLE sensors (
            port INTEGER PRIMARY KEY CHECK (port BETWEEN 1 AND 4),
            name TEXT NOT NULL DEFAULT '',
            usage TEXT NOT NULL DEFAULT 'unused' CHECK (usage IN ('unused', 'expected')),
            alarm_max_c REAL
        );
        CREATE TABLE fan_commands (
            port INTEGER PRIMARY KEY,
            sent_at TEXT NOT NULL,
            command TEXT NOT NULL,
            ok INTEGER NOT NULL,
            uncertain INTEGER NOT NULL,
            error TEXT
        );
        CREATE TABLE measurements_minute (
            minute INTEGER NOT NULL,
            source TEXT NOT NULL,
            avg REAL NOT NULL,
            min REAL NOT NULL,
            max REAL NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (source, minute)
        ) WITHOUT ROWID;
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            details TEXT NOT NULL
        );
        CREATE INDEX events_at ON events (at);
        INSERT INTO meta (key, value) VALUES ('config_revision', '1');
        INSERT INTO fans (port) VALUES (1), (2), (3), (4), (5), (6);
        INSERT INTO sensors (port) VALUES (1), (2), (3), (4);
        """,
    ),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SchemaTooNewError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @property
    def version(self) -> int:
        return self.query("PRAGMA user_version")[0][0]

    def migrate(self) -> int:
        """Apply pending migrations; returns the resulting schema version."""
        with self._lock:
            current = self.version
            if current > SCHEMA_VERSION:
                raise SchemaTooNewError(
                    f"database schema {current} is newer than this program ({SCHEMA_VERSION})"
                )
            for version, sql in MIGRATIONS:
                if version <= current:
                    continue
                with self.tx() as c:
                    for statement in _split(sql):
                        c.execute(statement)
                    c.execute(f"PRAGMA user_version = {int(version)}")
            return self.version

    def add_event(self, kind: str, **details: object) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO events (at, kind, details) VALUES (?, ?, ?)",
                (iso(utc_now()), kind, json.dumps(details, sort_keys=True)),
            )


def _split(script: str) -> list[str]:
    # The migration scripts contain no semicolons inside literals.
    return [s.strip() for s in script.split(";") if s.strip()]
