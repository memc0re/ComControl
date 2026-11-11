# Commander PRO protocol (own driver)

This document describes the part of the Corsair iCUE Commander PRO HID protocol (USB `1b1c:0c10`) used by the driver in `backend/src/comcontrol/commander/`. Other drivers (liquidctl, Linux `corsair-cpro`) were used only as a protocol reference; no code was copied. Every statement is marked with its verification status:

- **observed** – verified on a real device with the `commander-probe` tool,
- **reference** – taken from third-party sources only, not yet verified on a device.

Verification was done on a partially populated setup (one PWM fan, one temperature sensor). DC fans, further fan channels and sensors are **not verified** on hardware.

## Transport

| Property | Value | Status |
|---|---|---|
| Interface | Linux `hidraw`; the device is located in sysfs by its physical USB port path (e.g. `1-2.3`) | observed |
| Request | Output report, 64 bytes: command byte, arguments, rest padded with `0x00`. When writing to `hidraw`, a leading report ID byte `0x00` is prepended (65 bytes written). | observed |
| Response | Input report, 16 bytes. Byte 0 = status, payload from byte 1 | observed |
| Matching | The response contains **no command echo**. Responses are matched to requests by order only: exactly one request, then exactly one response. Stale reports are discarded before each request; access must be serialized. | observed |
| Timeout | The driver waits at most 1 s for a response, otherwise it reports an error | driver choice |

### Status byte

| Value | Meaning | Status |
|---|---|---|
| `0x00` | OK | observed |
| `0x01` | invalid command | reference |
| `0x10` | invalid arguments | reference |
| `0x11` | sensor not connected | reference |
| `0x12` | fan cannot be controlled this way | reference |

Any status other than `0x00` is an error. A response with the wrong length is invalid and is never treated as success.

## Command whitelist

All multi-byte values are big endian. Fans (0–5) and sensors (0–3) are **0-based** in the protocol; the device labels and `commander-probe` count from 1. The driver cannot build any other command (in particular RGB and firmware/bootloader writes).

### Read

| Command | Arguments | Response (from byte 1) | Status |
|---|---|---|---|
| `0x02` firmware | – | 3 bytes: major, minor, patch | observed |
| `0x06` bootloader | – | 2 bytes: major, minor | observed |
| `0x10` sensors connected | – | 4 bytes, per sensor `1` = connected, `0` = not | observed |
| `0x11` temperature | sensor | `s16`, 1/100 °C | observed |
| `0x12` voltage | rail (0 = +12 V, 1 = +5 V, 2 = +3.3 V) | `u16`, mV | observed (+3.3 V see below) |
| `0x20` fan modes | – | 6 bytes: `0` = disconnected, `1` = DC, `2` = PWM | observed (PWM, DC and disconnected; value `3` after mode `0`, see below) |
| `0x21` fan speed | fan | `u16`, RPM | observed |

Notes:

- A temperature of 0 alone does not prove a missing sensor; `0x10` must be checked as well. The driver never reports disconnected sensors or fans as 0 °C / 0 RPM.
- A DC fan at low fixed power can stall (observed: 0 RPM at 35 % and below, running at 50 %), so 0 RPM at a low target is not necessarily a fault.
- The +3.3 V rail can read far below 3.3 V (observed: a few hundred mV), probably because it is not wired on the device. This is not a device fault.

### Write

| Command | Arguments | Status |
|---|---|---|
| `0x23` fixed power | fan, percent (0–100) | observed (0 %, 50 %, 100 %) |
| `0x25` hardware curve | fan, sensor, 6 × temperature (`u16`, 1/100 °C), 6 × speed (`u16`, RPM) | observed |
| `0x28` set fan mode | `0x02`, fan, mode (`0`/`1`/`2`) | observed: PWM on a PWM fan and DC on a DC fan (accepted, read-back via `0x20` returns the mode, fixed power is retained); mode `0` (off) accepted, but `0x20` then reads `3` (undocumented) and the fan no longer reports a speed. Restored by setting DC again. Changing a fan to a mode it was not detected as is **not verified** |

A write response contains only the status byte. The controller does **not** confirm a target value and offers no read-back of power or curve. A sent command therefore only counts as "transmitted", not as "confirmed"; its effect shows only in the measured fan speed.

## Curve behaviour

| Observation | Status |
|---|---|
| Between two points the controller interpolates linearly (deviation at steady temperature typically below 10 RPM) | observed |
| Below the first point, the speed of the first point applies | observed |
| Above the last point, the speed of the last point applies, no overshoot | observed |
| A curve with fewer than 6 points, padded by repeating the last point, is accepted | observed |
| The curve keeps running after the writing program has exited | observed |
| Changing the control mode: the controller ramps the speed instead of jumping (approx. 10 s) | observed |
| The curve follows a temperature change within the curve range in both directions; on fast changes the speed lags a few seconds behind | observed |
| Behaviour when the sensor is unplugged during operation | **not verified** |

Padding: the driver requires strictly ascending temperatures. Repeated points therefore get the speed of the last point and a temperature raised by 0.01 °C each. The resulting effective curve is functionally identical to the entered one, because the speed of the last point applies above it anyway.
