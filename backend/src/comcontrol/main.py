"""Server entry point: `comcontrol-server`.

Runs exactly one Uvicorn worker - the process owns the controller.  A lock
file in the data directory prevents a second instance on the same data.
"""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys

from argon2 import PasswordHasher, Type

from .api.app import create_app
from .auth import AuthService, LoginRateLimiter
from .config_store import ConfigStore
from .db import Database
from .hardware.adapter import HardwareAdapter, HidrawConnector, SimulatorConnector
from .hardware.simulator import PRESETS, make_preset
from .runtime import Runtime
from .settings import Settings

log = logging.getLogger("comcontrol")


def build(settings: Settings, *, simulator=None):
    """Wire database, adapter, runtime and app.  Returns (app, runtime, db)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.data_dir / "comcontrol.sqlite3")
    db.migrate()
    store = ConfigStore(db)
    if settings.simulate:
        sim = simulator or make_preset(settings.simulate, animate=True)
        connector = SimulatorConnector(sim)
    else:
        if store.device_usb_path() is None and settings.usb_path:
            store.set_device_usb_path(settings.usb_path, None)
        connector = HidrawConnector(store.device_usb_path())
    adapter = HardwareAdapter(connector, raw_log=settings.raw_log)
    rt = Runtime(settings, db, adapter)
    hasher = PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_kib,
        parallelism=settings.argon2_parallelism,
        type=Type.ID,
    )
    auth = AuthService(db, hasher, session_seconds=settings.session_hours * 3600)
    limiter = LoginRateLimiter(settings.login_max_failures, settings.login_window_s)
    return create_app(rt, auth, limiter), rt, db


def _lock_data_dir(settings: Settings) -> int:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(settings.data_dir / "comcontrol.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(f"another ComControl instance uses {settings.data_dir}") from None
    return fd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="comcontrol-server", description="ComControl backend")
    ap.add_argument("--simulate", choices=PRESETS, help="use the simulator (explicit, shown in the UI)")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--data-dir")
    ap.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    if args.simulate:
        settings.simulate = args.simulate
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if args.data_dir:
        from pathlib import Path

        settings.data_dir = Path(args.data_dir)
    if settings.simulate and settings.simulate not in PRESETS:
        raise SystemExit(f"COMCONTROL_SIMULATE must be one of {', '.join(PRESETS)}")

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.Formatter.converter = __import__("time").gmtime  # log timestamps in UTC
    if not settings.raw_log:
        logging.getLogger("comcontrol.commander.raw").setLevel(logging.INFO)

    _lock = _lock_data_dir(settings)  # noqa: F841 - held for the process lifetime
    app, _, _ = build(settings)
    if settings.simulate:
        log.warning("SIMULATION MODE (%s): no real hardware is controlled", settings.simulate)

    import uvicorn

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        proxy_headers=False,  # forwarded headers are not trusted
        server_header=False,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
