# Docker

The image is built in two stages: the web UI is built with Node.js, the runtime image contains only the Python backend (dependencies installed from the hash-pinned `backend/requirements.lock`) and the static UI. Base images are pinned by digest. The container runs as an unprivileged user with a read-only root filesystem, no capabilities and `no-new-privileges`; `privileged: true` is not needed.

## Host preparation

The same as for native operation (see the main README):

- udev rule granting access to the Commander PRO (`1b1c:0c10`) **only**, e.g. group `plugdev`, mode `0660`. Other hidraw devices stay root-only, so the container cannot open them.
- `corsair_cpro` must not be bound to the controller (`blacklist corsair_cpro`).

## Configuration

```sh
cp .env.example .env
commander-probe list          # or: ls -l /sys/class/hidraw/*/device  → USB port path
grep hidraw /proc/devices     # → HIDRAW_MAJOR
getent group plugdev          # → COMCONTROL_GID (group of the controller's hidraw node)
```

Set `COMCONTROL_USB_PATH`, `HIDRAW_MAJOR` and `COMCONTROL_GID` in `.env`. For LAN access set `COMCONTROL_BIND=0.0.0.0` and `COMCONTROL_ALLOWED_HOSTS` to the host names used in the browser.

## Running

`compose.yaml` uses the published image `ghcr.io/memc0re/comcontrol:latest`:

```sh
docker compose pull
docker compose up -d
docker compose logs -f
```

### Published image

The workflow `.github/workflows/docker-publish.yml` in the GitHub repository [memc0re/ComControl](https://github.com/memc0re/ComControl) builds the image from the `Dockerfile` and pushes it to the GitHub Container Registry when a release is published. Release tags must be semantic versions (e.g. `v0.2.0`); the image then gets the tags `0.2.0`, `0.2` and `latest` (pre-releases do not move `latest`).

- Until the first release is published, no image exists; build locally (see below).
- If the package is private on GitHub, log in first: `docker login ghcr.io` with a personal access token that has `read:packages`.
- To pin a version instead of following `latest`, change the `image:` line in `compose.yaml`, e.g. to `ghcr.io/memc0re/comcontrol:0.2`.

### Building locally

`compose.build.yaml` replaces the published image with a build from the current checkout:

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

Open `http://<host>:8080`, log in with `admin` / `admin` on the first start and change the password.

## How device access works

- The hidraw node number (`/dev/hidrawN`) can change on every reconnect, so passing a single device node is not enough. The host's `/dev` is mounted read-only at `/hostdev` (`COMCONTROL_DEV_DIR`), and a device cgroup rule allows hidraw character devices. A reconnected controller with a new number is therefore visible without restarting the container.
- The controller is still identified only by its USB port path from sysfs; the backend opens nothing else. The device node is locked with `flock`, so a `commander-probe` on the host cannot talk to the controller at the same time.

## Data

All persistent data (database with configuration, history, alarms, fail-safe state and the administrator account) lives in the volume `comcontrol-data` mounted at `/data`. Keep the volume when updating:

```sh
docker compose down          # not "down -v": that deletes the data
docker compose pull
docker compose up -d
```

## Health

The image has a health check on `/api/v1/health`. It reports a reachable backend; whether the hardware works is in the response body (`"hardware": "ok" | "unavailable"`) and in the UI.

## Verification status

On the development setup (one PWM fan, one sensor) the locally built container passed the hardware acceptance test (`COMCONTROL_HW_BASE_URL=http://127.0.0.1:8080 npm run test:hardware` against a fresh volume): live values in the UI, 5-second updates, fixed power and curve changes. Data persisted across container re-creation (changed password kept, stored settings re-applied on start). **Not verified** in the container: unplugging and reconnecting the USB cable, resume from standby.
