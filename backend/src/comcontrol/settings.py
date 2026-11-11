"""Runtime settings from environment variables (prefix COMCONTROL_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8080
    # Simulator preset (empty|minimal|gaps|full).  None = real hardware.
    simulate: str | None = None
    # Initial USB port path, used only while no device has been assigned yet.
    usb_path: str | None = None
    static_dir: Path | None = None
    # Extra origins allowed for state-changing requests (e.g. Vite dev server).
    allowed_origins: list[str] = field(default_factory=list)
    # If set, requests with another Host header are rejected (DNS rebinding).
    allowed_hosts: list[str] = field(default_factory=list)
    raw_log: bool = False
    poll_interval: float = 5.0
    stale_after: float = 15.0
    hardware_timeout: float = 5.0
    session_hours: float = 8.0
    # argon2id parameters (RFC 9106 "low memory" profile by default)
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4
    retention_days: int = 365
    login_max_failures: int = 5
    login_window_s: float = 300.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        s = cls()
        if v := e.get("COMCONTROL_DATA_DIR"):
            s.data_dir = Path(v)
        s.host = e.get("COMCONTROL_HOST", s.host)
        s.port = int(e.get("COMCONTROL_PORT", s.port))
        s.simulate = e.get("COMCONTROL_SIMULATE") or None
        s.usb_path = e.get("COMCONTROL_USB_PATH") or None
        if v := e.get("COMCONTROL_STATIC_DIR"):
            s.static_dir = Path(v)
        s.allowed_origins = _list(e.get("COMCONTROL_ALLOWED_ORIGINS"))
        s.allowed_hosts = _list(e.get("COMCONTROL_ALLOWED_HOSTS"))
        s.raw_log = _bool(e.get("COMCONTROL_RAW_LOG"))
        return s
