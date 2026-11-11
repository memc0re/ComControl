"""Linux hidraw discovery and transport (stdlib only).

Only the Commander PRO (USB 1b1c:0c10) is ever opened.  Other Corsair devices
(AIO, PSU) may be attached to the same machine and must never be touched.
"""

from __future__ import annotations

import fcntl
import os
import re
import select
import time
from dataclasses import dataclass
from pathlib import Path

from .protocol import REPORT_LENGTH, RESPONSE_LENGTH

VENDOR_ID = 0x1B1C
PRODUCT_ID = 0x0C10
DEFAULT_TIMEOUT = 1.0

_SYSFS = Path("/sys/class/hidraw")
_HID_ID = re.compile(r"^HID_ID=[0-9A-Fa-f]+:([0-9A-Fa-f]+):([0-9A-Fa-f]+)$", re.M)
_USB_PORT = re.compile(r"^\d+-\d+(\.\d+)*$")


class TransportError(Exception):
    """I/O failure or timeout while talking to the device."""


@dataclass(frozen=True)
class DeviceEntry:
    node: str       # e.g. /dev/hidraw8 (may change, not an identity)
    usb_path: str   # e.g. 1-2.3 (physical port path, used as identity)


def find_devices(sysfs: Path = _SYSFS) -> list[DeviceEntry]:
    """List Commander PRO hidraw nodes; reads only sysfs, never opens a device."""
    found = []
    for entry in sorted(sysfs.glob("hidraw*")):
        try:
            uevent = (entry / "device" / "uevent").read_text()
            match = _HID_ID.search(uevent)
            if not match or (int(match[1], 16), int(match[2], 16)) != (VENDOR_ID, PRODUCT_ID):
                continue
            parts = (entry / "device").resolve().parts
        except OSError:
            continue
        ports = [p for p in parts if _USB_PORT.match(p)]
        if ports:
            found.append(DeviceEntry(f"/dev/{entry.name}", ports[-1]))
    return found


def kernel_driver(entry: DeviceEntry, sysfs: Path = _SYSFS) -> str | None:
    """Name of the kernel HID driver bound to the device (sysfs only).

    `hid-generic` is expected.  `corsair-cpro` means the hwmon driver talks to
    the same device and would interleave its own requests with ours.
    """
    link = sysfs / Path(entry.node).name / "device" / "driver"
    try:
        return link.resolve(strict=True).name
    except OSError:
        return None


class DeviceBusyError(TransportError):
    """Another process holds the device lock (e.g. a second backend or the probe tool)."""


class PermissionDeniedError(TransportError):
    """The hidraw node exists but may not be opened by this user."""


class HidrawTransport:
    """One exchange = write one output report, read one input report.

    The open node is locked with flock(LOCK_EX) so that cooperating processes
    (the backend and `commander-probe`) never interleave requests.
    """

    def __init__(self, entry: DeviceEntry, timeout: float = DEFAULT_TIMEOUT) -> None:
        # Re-verify identity right before opening: the node number can change.
        if entry not in find_devices():
            raise TransportError(f"{entry.node} is not a Commander PRO at {entry.usb_path}")
        self.entry = entry
        self.timeout = timeout
        try:
            self._fd: int | None = os.open(entry.node, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise PermissionDeniedError(f"cannot open {entry.node}: {exc.strerror}") from exc
        except OSError as exc:
            raise TransportError(f"cannot open {entry.node}: {exc.strerror}") from exc
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise DeviceBusyError(f"{entry.node} is in use by another process") from exc

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "HidrawTransport":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _drain(self) -> None:
        while self._fd is not None and select.select([self._fd], [], [], 0)[0]:
            try:
                os.read(self._fd, 64)
            except BlockingIOError:
                return

    def exchange(self, frame: bytes) -> bytes:
        if self._fd is None:
            raise TransportError("device is closed")
        if len(frame) != REPORT_LENGTH:
            raise ValueError(f"frame must be {REPORT_LENGTH} bytes")
        self._drain()  # stale reports must not be mistaken for the answer
        try:
            os.write(self._fd, b"\x00" + frame)  # leading 0 = "no report id"
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self._fd], [], [], remaining)[0]:
                    raise TransportError("timeout waiting for response")
                try:
                    return os.read(self._fd, 64)
                except BlockingIOError:
                    continue
        except OSError as exc:
            raise TransportError(f"I/O error: {exc.strerror}") from exc
