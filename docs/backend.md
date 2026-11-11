# Backend

The backend (`backend/`, Python package `comcontrol`) owns the Commander PRO, polls it every 5 seconds, stores configuration and per-minute history in SQLite and serves the JSON API under `/api/v1/`. The web UI is not implemented yet.

## Setup

Python ≥ 3.11 on Linux. Dependencies are pinned with hashes in `requirements.lock` (runtime) and `requirements-dev.lock` (runtime + tests):

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
```

The lock files are generated with pip-tools from `pyproject.toml`:

```sh
.venv/bin/pip-compile --generate-hashes --strip-extras --no-emit-index-url -o requirements.lock pyproject.toml
.venv/bin/pip-compile --generate-hashes --strip-extras --no-emit-index-url --extra dev -o requirements-dev.lock pyproject.toml
```

## Running

```sh
# real hardware: the controller is selected by its physical USB port path
COMCONTROL_USB_PATH=1-2.3 .venv/bin/comcontrol-server --data-dir /var/lib/comcontrol

# simulator (explicit; the API reports "simulated": true everywhere)
.venv/bin/comcontrol-server --simulate minimal --data-dir ./data-sim
```

`commander-probe list` shows the USB port paths of connected Commander PRO devices without opening them.

The server always runs exactly one Uvicorn worker, because one process must own the controller. A lock file in the data directory prevents a second instance on the same data; the hidraw device itself is locked with `flock`, so the backend and `commander-probe` never talk to the controller at the same time.

On the first login use `admin` / `admin` and change the password immediately. The default account is created only when no administrator exists; restarts and updates never reset the password.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `COMCONTROL_DATA_DIR` | `data` | Database and lock file. Must persist across updates. |
| `COMCONTROL_HOST` / `COMCONTROL_PORT` | `127.0.0.1` / `8080` | Listen address. Use `0.0.0.0` for LAN access. |
| `COMCONTROL_USB_PATH` | – | Initial USB port path; used only while no device has been assigned. Later changes go through the API (`/device/assign`). |
| `COMCONTROL_SIMULATE` | – | Simulator preset: `empty`, `minimal`, `gaps`, `full`. |
| `COMCONTROL_STATIC_DIR` | – | Built web UI to serve. |
| `COMCONTROL_ALLOWED_ORIGINS` | – | Extra origins for state-changing requests, comma separated (e.g. `http://localhost:5173` for the Vite dev server). |
| `COMCONTROL_ALLOWED_HOSTS` | – | If set, requests with another `Host` header are rejected (protection against DNS rebinding). Recommended for LAN use. |
| `COMCONTROL_RAW_LOG` | off | Log raw HID frames at debug level (`--log-level debug`). Frames contain no credentials. |

Forwarded headers (`X-Forwarded-For`, …) are never trusted.

## Hardware access

- Only USB `1b1c:0c10` at exactly the configured port path is opened. Other Corsair devices are never touched, and a Commander PRO found at a different port is not used automatically; it is reported as a candidate for an explicit reassignment.
- Connection problems are reported with a stable code in `GET /api/v1/device` (`device_not_assigned`, `device_not_found`, `kernel_driver_conflict`, `permission_denied`, `device_busy`, `device_io_error`, `hardware_timeout`, …). A conflict is reported, never forced.
- All device I/O runs on one worker thread with time limits; the HTTP event loop never waits on USB without a limit. Browsers read only the cached state.
- A hardware error never switches to simulated values.

## Behaviour

- **Measurements:** every 5 s. Each value carries unit, status (`ok`, `not_connected`, `error`, `stale`, `no_data`) and the UTC time of its last valid reading. Values older than 15 s (monotonic clock) are `stale`. Missing or invalid values are `null`, never 0 °C / 0 RPM; a measured 0 RPM of a detected fan is a valid value.
- **Channel status:** the configured usage (`unused` / `expected`) is kept separate from what the controller reports. Expected channels are `present`, `expected_missing`, `error` or `stale`.
- **History:** per source (sensor, fan, rail) and UTC minute: mean, min, max and count of valid readings. Data older than 365 days is deleted.
- **Control:** each expected fan has either fixed power (0–100 %) or a hardware curve. Changes are sent only by an explicit *apply* request. The stored configuration changes only after the controller accepted all commands; a failed apply leaves the previous configuration and records the attempt (including whether the hardware may have been changed partially). The controller does not confirm values by read-back, so a successful apply means *transmitted*, not *verified*.
- **Curves:** 1–6 points, strictly ascending temperatures (0.01 °C resolution), non-decreasing RPM, last point equal to the channel's configured maximum RPM. Shorter curves are padded to six points; the effective curve is returned. Curves that cannot be represented are rejected with a reason.
- **Activation:** a channel switched from `unused` to `expected` starts at 100 % fixed power.
- **Start and reconnect:** after a regular start, stored settings are re-applied once the population has been read. After a lost connection, 100 % is requested for all expected fans first; the stored profile is not restored automatically.
- **Concurrency:** every configuration change names the configuration revision it is based on; a stale revision returns HTTP 409.

## API overview

The OpenAPI document is served at `/api/v1/openapi.json` (interactive docs at `/api/v1/docs`). Errors use the format `{"error": {"code": "...", "details": {...}}}`; the UI translates the codes.

| Method and path | Purpose |
|---|---|
| `GET /health` | Backend and hardware health (no login) |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/session` | Session handling |
| `POST /auth/password` | Change the administrator password |
| `GET /device`, `GET /device/candidates`, `POST /device/assign` | Controller information, diagnostics, reassignment |
| `GET /state` | Cached live state |
| `GET /config` | Configuration and revision |
| `PUT /fans/{id}` | Name, usage, maximum RPM, DC start threshold |
| `POST /fans/{id}/apply` | Apply fan mode and fixed power or curve |
| `PUT /sensors/{id}` | Name, usage, temperature alarm limit |
| `POST /curves/check` | Validate a curve and show its effective form |

Authentication: opaque session token in an `HttpOnly`, `SameSite=Strict` cookie (no `Secure` flag, HTTP operation in the LAN), 8 hours lifetime; only token hashes are stored. State-changing requests need the session's CSRF token in the `X-CSRF-Token` header and must come from an allowed origin. Failed logins are rate limited per client address.

## Tests

```sh
cd backend && .venv/bin/pytest -q
```

The tests use temporary databases and the simulator; they never access real hardware. The simulator reproduces the documented configurations (no connections, one fan with one sensor, channels with gaps, full population) and behaviour observed on real hardware. Behaviour that was not verified on hardware (for example a curve whose sensor is unplugged) is a simulator choice and does not count as hardware-confirmed.
