"""Adapter, simulator and device selection - no real hardware."""

from pathlib import Path

import pytest

from comcontrol.commander import hidraw
from comcontrol.commander.hidraw import DeviceEntry
from comcontrol.hardware import adapter as adapter_mod
from comcontrol.hardware.adapter import (
    ConnectError,
    ConnectionLost,
    HardwareAdapter,
    HidrawConnector,
    SimulatorConnector,
)
from comcontrol.hardware.simulator import PRESETS, make_preset


def connected(preset: str):
    sim = make_preset(preset)
    ad = HardwareAdapter(SimulatorConnector(sim))
    ad.connect()
    return sim, ad


@pytest.mark.parametrize(
    ("preset", "sensors", "fans"),
    [
        ("empty", [False] * 4, ["disconnected"] * 6),
        ("minimal", [True, False, False, False], ["pwm"] + ["disconnected"] * 5),
        ("gaps", [True, False, True, False], ["pwm", "disconnected", "dc", "disconnected", "disconnected", "pwm"]),
        ("full", [True] * 4, ["pwm", "pwm", "dc", "pwm", "pwm", "dc"]),
    ],
)
def test_presets_population(preset, sensors, fans):
    _, ad = connected(preset)
    poll = ad.poll()
    assert poll.sensors_connected == sensors
    assert poll.fan_modes == fans
    # not connected -> not read at all (no 0 °C / 0 rpm placeholders)
    assert [t is not None for t in poll.temperatures] == sensors
    assert [r is not None for r in poll.rpms] == [f != "disconnected" for f in fans]


def test_all_presets_known():
    assert set(PRESETS) == {"empty", "minimal", "gaps", "full"}
    with pytest.raises(ValueError):
        make_preset("bogus")


def test_simulator_reports_firmware_and_simulated_flag():
    _, ad = connected("minimal")
    assert ad.simulated and ad.info.firmware == "0.9.212" and ad.info.usb_path == "simulated"


def test_fixed_percent_and_dc_stall():
    sim, ad = connected("gaps")
    assert ad.apply_fan(0, mode=None, percent=50).ok
    assert ad.poll().rpms[0].value == 750
    # DC fan below its start threshold stands still: valid 0 rpm, not an error
    assert ad.apply_fan(2, mode=None, percent=30).ok
    r = ad.poll().rpms[2]
    assert r.value == 0 and r.error is None


def test_curve_executed_by_simulated_controller():
    sim, ad = connected("minimal")
    sim.sensors[0].temperature = 25.0
    pts = [(20, 500), (30, 1500), (30.01, 1500), (30.02, 1500), (30.03, 1500), (30.04, 1500)]
    assert ad.apply_fan(0, mode=None, curve=(0, pts)).ok
    assert ad.poll().rpms[0].value == 1000


def test_mode_command_sent_before_control():
    sim, ad = connected("minimal")
    sim.sent.clear()
    out = ad.apply_fan(0, mode="pwm", percent=80)
    assert out.ok and [s["command"] for s in out.steps] == ["set_mode", "set_percent"]
    assert [f[0] for f in sim.sent] == [0x28, 0x23]


def test_sensor_read_error_is_per_value():
    sim, ad = connected("minimal")
    sim.faults.status_error_reads = 1
    poll = ad.poll()
    assert poll.temperatures[0].error == "device_status_10" and poll.temperatures[0].value is None
    assert poll.rpms[0].error is None


def test_corrupt_response_is_invalid_not_success():
    sim, ad = connected("minimal")
    sim.faults.corrupt_responses = 1
    out = ad.apply_fan(0, mode=None, percent=40)
    assert not out.ok and out.error == "invalid_response" and out.uncertain


def test_partial_write_marked_uncertain():
    sim, ad = connected("minimal")
    # mode accepted, percent rejected by the device (status 0x10 for >100 is
    # unreachable through the validated API, so corrupt the second answer)
    original = sim._handle
    calls = []

    def handle(frame):
        calls.append(frame[0])
        resp = original(frame)
        return bytes([0x12]) + resp[1:] if frame[0] == 0x23 else resp

    sim._handle = handle
    out = ad.apply_fan(0, mode="pwm", percent=40)
    assert not out.ok and out.uncertain and out.error == "device_status_12"
    assert out.steps[0]["ok"] and not out.steps[1]["ok"]


def test_unplug_during_poll_disconnects():
    sim, ad = connected("minimal")
    sim.faults.unplugged = True
    with pytest.raises(ConnectionLost):
        ad.poll()
    assert not ad.connected
    with pytest.raises(ConnectError) as exc:
        ad.connect()
    assert exc.value.code == "device_not_found"


def test_unplug_during_write_is_uncertain():
    sim, ad = connected("minimal")
    sim.faults.unplugged = True
    out = ad.apply_fan(0, mode=None, percent=100)
    assert not out.ok and out.uncertain and not ad.connected


def test_whitelist_only_commands_reach_the_simulator():
    sim, ad = connected("full")
    ad.poll()
    ad.apply_fan(0, mode="pwm", percent=60)
    allowed = {0x02, 0x06, 0x10, 0x11, 0x12, 0x20, 0x21, 0x23, 0x25, 0x28}
    assert {f[0] for f in sim.sent} <= allowed


# --- real connector: device selection and diagnostics (fake sysfs) ----------

def fake_sysfs(root: Path, devices: list[tuple[str, str, str, str]]) -> Path:
    """devices: (hidraw name, vid:pid, usb port, driver)."""
    sysfs = root / "class" / "hidraw"
    sysfs.mkdir(parents=True)
    for name, vidpid, port, driver in devices:
        vid, pid = vidpid.split(":")
        devdir = root / "devices" / "usb1" / port / f"{port}:1.0" / f"0003:{vid}:{pid}.0001"
        devdir.mkdir(parents=True)
        (devdir / "uevent").write_text(f"DRIVER={driver}\nHID_ID=0003:0000{vid}:0000{pid}\n")
        drv = root / "bus" / "hid" / "drivers" / driver
        drv.mkdir(parents=True, exist_ok=True)
        (devdir / "driver").symlink_to(drv)
        (sysfs / name).mkdir()
        (sysfs / name / "device").symlink_to(devdir)
    return sysfs


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    def install(devices):
        path = fake_sysfs(tmp_path, devices)
        monkeypatch.setattr(hidraw, "_SYSFS", path)
        monkeypatch.setattr(hidraw.find_devices, "__defaults__", (path,))
        monkeypatch.setattr(hidraw.kernel_driver, "__defaults__", (path,))
        return path

    return install


def test_only_commander_pro_is_listed(sysfs):
    sysfs([
        ("hidraw1", "1b1c:0c12", "1-3", "hid-generic"),   # AIO - must be ignored
        ("hidraw2", "1b1c:1c0b", "1-5", "corsairpsu"),    # PSU - must be ignored
        ("hidraw3", "1b1c:0c10", "1-4.2", "hid-generic"),
    ])
    assert hidraw.find_devices() == [DeviceEntry("/dev/hidraw3", "1-4.2")]


def test_connector_requires_assignment(sysfs):
    sysfs([("hidraw3", "1b1c:0c10", "1-4.2", "hid-generic")])
    with pytest.raises(ConnectError) as exc:
        HidrawConnector(None).open()
    assert exc.value.code == "device_not_assigned" and exc.value.details["candidates"] == ["1-4.2"]


def test_connector_never_opens_device_at_other_path(sysfs, monkeypatch):
    sysfs([("hidraw3", "1b1c:0c10", "1-4.3", "hid-generic")])
    opened = []
    monkeypatch.setattr(adapter_mod, "HidrawTransport", lambda *a, **k: opened.append(a))
    with pytest.raises(ConnectError) as exc:
        HidrawConnector("1-4.2").open()
    assert exc.value.code == "device_not_found" and exc.value.details["candidates"] == ["1-4.3"]
    assert opened == []


def test_kernel_driver_conflict_is_diagnosed(sysfs, monkeypatch):
    sysfs([("hidraw3", "1b1c:0c10", "1-4.2", "corsair-cpro")])
    opened = []
    monkeypatch.setattr(adapter_mod, "HidrawTransport", lambda *a, **k: opened.append(a))
    with pytest.raises(ConnectError) as exc:
        HidrawConnector("1-4.2").open()
    assert exc.value.code == "kernel_driver_conflict" and opened == []


def test_permission_denied_is_diagnosed(sysfs, monkeypatch):
    sysfs([("hidraw3", "1b1c:0c10", "1-4.2", "hid-generic")])

    def deny(*a, **k):
        raise hidraw.PermissionDeniedError("cannot open /dev/hidraw3: Permission denied")

    monkeypatch.setattr(adapter_mod, "HidrawTransport", deny)
    with pytest.raises(ConnectError) as exc:
        HidrawConnector("1-4.2").open()
    assert exc.value.code == "permission_denied"


def test_real_connector_failure_never_falls_back_to_simulation(sysfs):
    sysfs([])
    ad = HardwareAdapter(HidrawConnector("1-4.2"))
    with pytest.raises(ConnectError):
        ad.connect()
    assert not ad.simulated and not ad.connected
