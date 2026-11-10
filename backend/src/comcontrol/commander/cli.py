"""Minimal probe tool for the Commander PRO (step 1 of the hardware proof).

Read-only by default.  Anything that changes device state needs --write.
Fans and sensors are numbered 1-based, as on the controller's connectors.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import protocol as p
from .device import CommanderPro
from .hidraw import HidrawTransport, TransportError, find_devices
from .protocol import FanMode, ProtocolError

_MODES = {"off": FanMode.DISCONNECTED, "dc": FanMode.DC, "pwm": FanMode.PWM}


def _open(args: argparse.Namespace) -> HidrawTransport:
    devices = find_devices()
    if args.usb_path:
        devices = [d for d in devices if d.usb_path == args.usb_path]
    if not devices:
        raise SystemExit("no Commander PRO (1b1c:0c10) found")
    if len(devices) > 1:
        paths = ", ".join(d.usb_path for d in devices)
        raise SystemExit(f"several Commander PRO found ({paths}); choose with --usb-path")
    return HidrawTransport(devices[0])


def _require_write(args: argparse.Namespace) -> None:
    if not args.write:
        raise SystemExit("this command changes device state; add --write to confirm")


def _info(dev: CommanderPro) -> None:
    print("firmware  :", ".".join(map(str, dev.firmware())))
    print("bootloader:", ".".join(map(str, dev.bootloader())))
    for i, ok in enumerate(dev.temp_connected(), 1):
        print(f"temp {i}    : {'connected' if ok else '-'}")
    for i, mode in enumerate(dev.fan_modes(), 1):
        print(f"fan {i}     : {getattr(mode, 'name', f'unknown({mode})')}")


def _status(dev: CommanderPro) -> None:
    connected = dev.temp_connected()
    modes = dev.fan_modes()
    for i, ok in enumerate(connected):
        if ok:
            print(f"temp {i + 1}: {dev.temperature(i):.2f} °C")
    for i, mode in enumerate(modes):
        if mode in (FanMode.DC, FanMode.PWM):
            print(f"fan {i + 1}: {dev.fan_rpm(i)} rpm")
    for i, rail in enumerate(("+12V", "+5V", "+3.3V")):
        print(f"{rail}: {dev.voltage(i):.3f} V")


def _parse_points(raw: list[str]) -> list[tuple[float, int]]:
    points = []
    for item in raw:
        temp, _, rpm = item.partition(":")
        points.append((float(temp), int(rpm)))
    if not 1 <= len(points) <= p.CURVE_POINTS:
        raise SystemExit(f"give 1..{p.CURVE_POINTS} points as TEMP:RPM")
    while len(points) < p.CURVE_POINTS:  # pad by repeating the last point
        last_t, last_r = points[-1]
        points.append((last_t + 0.01, last_r))
    return points


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="commander-probe", description=__doc__)
    ap.add_argument("--usb-path", help="physical USB port path, e.g. 1-4.2")
    ap.add_argument("--write", action="store_true", help="allow state-changing commands")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list Commander PRO devices (sysfs only, no device access)")
    sub.add_parser("info", help="firmware, sensors and fan modes")
    st = sub.add_parser("status", help="temperatures, rpm and voltages")
    st.add_argument("--watch", type=float, metavar="SECONDS", help="repeat every N seconds")
    pc = sub.add_parser("set-percent", help="fixed fan power")
    pc.add_argument("fan", type=int)
    pc.add_argument("percent", type=int)
    md = sub.add_parser("set-mode", help="fan connector mode")
    md.add_argument("fan", type=int)
    md.add_argument("mode", choices=_MODES)
    cv = sub.add_parser("set-curve", help="hardware curve, points as TEMP:RPM")
    cv.add_argument("fan", type=int)
    cv.add_argument("sensor", type=int)
    cv.add_argument("points", nargs="+")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for d in find_devices():
            print(f"{d.node}  usb-path {d.usb_path}")
        return 0

    try:
        with _open(args) as transport:
            dev = CommanderPro(transport)
            if args.cmd == "info":
                _info(dev)
            elif args.cmd == "status":
                while True:
                    _status(dev)
                    if not args.watch:
                        break
                    print("--")
                    time.sleep(args.watch)
            else:
                _require_write(args)
                if args.cmd == "set-percent":
                    dev.set_fixed_percent(args.fan - 1, args.percent)
                elif args.cmd == "set-mode":
                    dev.set_fan_mode(args.fan - 1, _MODES[args.mode])
                else:
                    dev.set_curve(args.fan - 1, args.sensor - 1, _parse_points(args.points))
                print("sent (not confirmed by read-back)")
    except (TransportError, ProtocolError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
