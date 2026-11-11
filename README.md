# ComControl

Web interface for monitoring and controlling the fans of a server rack through a USB-connected **Corsair iCUE Commander PRO**.

> [!WARNING]
> **Use at your own risk.** ComControl directly controls the fans and therefore the cooling of server components. Incorrect settings, software bugs, or a failure of the backend, the USB connection or the controller can lead to insufficient cooling, overheating and hardware damage. This version has **no independent watchdog** that reliably switches the fans to full power in case of a failure.
>
> The software is provided **without any warranty**, in particular without any guarantee of correct operation or fitness for a particular purpose, and without liability for damage to hardware, data or other property (see also sections 15 and 16 of the GPLv3). Monitor your system's temperatures independently of ComControl.
>
> ComControl is not a Corsair product and is not affiliated with Corsair.

## Project status

ComControl is under development and **not ready for use**: the hardware acceptance test with a real controller is still outstanding.

What exists so far:

- a protocol layer for the Commander PRO in Python (direct access via Linux `hidraw`),
- the diagnostic tool `commander-probe` for reading values and setting them for testing,
- a description of the protocol subset in use, including its verification status: [`docs/protocol.md`](docs/protocol.md),
- the backend (FastAPI): hardware adapter and simulator, 5-second polling, SQLite persistence with per-minute history, login, channel configuration, fixed power and hardware curves, alarms and the latched fail-safe reaction (100 %) via the JSON API – see [`docs/backend.md`](docs/backend.md),
- the web UI (German and English, light and dark, desktop and phone) – see [`docs/frontend.md`](docs/frontend.md).

Verified on real hardware so far: readings, fixed fan power, hardware curves and setting PWM mode on a PWM fan; fixed power, hardware curve and mode setting on a single DC fan; the backend reading live values from a real controller. Several DC channels, multi-channel operation and the full population are **not verified**.

## Requirements

- Linux with `hidraw` support, Python ≥ 3.11
- Read/write access to the Commander PRO's `hidraw` device (USB `1b1c:0c10`), e.g. through a udev rule that grants access to **this device only**:

  ```
  SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1b1c", ATTRS{idProduct}=="0c10", GROUP="plugdev", MODE="0660"
  ```

- The kernel module `corsair_cpro` must not use the device at the same time (e.g. `blacklist corsair_cpro` in `/etc/modprobe.d/`). Any other program talking to the controller must be stopped.

## Diagnostic tool

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .

.venv/bin/commander-probe list            # sysfs only, does not open any device
.venv/bin/commander-probe info            # firmware, sensors, fan modes
.venv/bin/commander-probe status --watch 5
```

Fans and sensors are numbered from 1, as labelled on the device. Commands that change device state require `--write`:

```sh
.venv/bin/commander-probe --write set-percent 1 100
.venv/bin/commander-probe --write set-curve 1 1 25:600 35:1000 45:1500
```

The controller does not confirm a sent command by read-back; only the measured fan speed shows its effect.

Tests (no hardware needed; they use a fake transport, the simulator and temporary databases):

```sh
cd backend && .venv/bin/pytest -q
```

## License

ComControl is licensed under the **GNU General Public License v3.0 or later** (GPL-3.0-or-later), see [`LICENSE`](LICENSE).

The driver is an independent implementation. Other projects (including liquidctl and the Linux driver `corsair-cpro`) were used only as a protocol reference; no code was copied from them.
