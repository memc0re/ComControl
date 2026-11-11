# Web UI

The web UI (`frontend/`) is a SvelteKit single page application (TypeScript, Tailwind CSS, shadcn-svelte components) built with `@sveltejs/adapter-static`. The backend serves the built files; Node.js is only needed to build and test.

## Toolchain

Node.js 26 LTS and npm 11. Dependencies are locked in `package-lock.json`; install them reproducibly with `npm ci`. Fonts, icons, scripts and styles are bundled; the UI needs no internet access or CDN.

`npm audit` currently reports low-severity advisories in build-time tooling (`deepmerge` via the Svelte Vite plugin) without an available fix. They do not reach the built static files.

## Commands

```sh
cd frontend
npm ci
npm run dev          # Vite dev server, proxies /api/v1 to http://127.0.0.1:8080 (COMCONTROL_BACKEND)
npm run check        # svelte-check / TypeScript
npm run build        # static build in build/
npm run test:unit    # translation completeness (node --test)
npm run test:e2e     # build + Playwright end-to-end tests
npm test             # all of the above
```

For the dev server, start the backend separately and allow the Vite origin, e.g.:

```sh
COMCONTROL_ALLOWED_ORIGINS=http://localhost:5173 backend/.venv/bin/comcontrol-server --simulate gaps --data-dir ./data-sim
```

To serve the built UI from the backend, set `COMCONTROL_STATIC_DIR=frontend/build`. Only the known client routes (`/`, `/login`, `/channels`, `/curves`, `/history`, `/alarms`, `/settings`) get `index.html`; unknown paths and missing assets return 404.

## End-to-end tests

`npm run test:e2e` starts the real backend in simulation mode (preset `gaps`) with a fresh temporary database (`tests/e2e/server.mjs`) and runs Playwright against the built UI: desktop in light mode, and a narrow phone viewport in dark mode. The tests cover login, live values, language and theme switches, channel setup, fixed power and curve editing, configuration conflicts, alarms, the fail-safe reaction and its release, controller loss and reconnection, backend loss, the disclaimer and password change. Faults are injected through `/api/v1/simulator`, which exists only in simulation mode.

These tests never use real hardware. They do not replace the hardware acceptance test with a real controller.

## Conventions

- All texts live in `src/lib/i18n/en.ts` and `src/lib/i18n/de.ts`; both must be complete (type check plus `tests/unit/i18n.test.ts`). The API returns error codes; the UI translates them.
- States are always shown with text and an icon, never by colour alone.
- Only the language and colour scheme are stored in the browser (`localStorage`); credentials and session tokens are not.

## Hardware acceptance test

`npm run test:hardware` runs `tests/hardware/acceptance.hw.ts` against the **real** controller. It must be selected explicitly with the controller's USB port path and uses a fresh temporary database:

```sh
COMCONTROL_HW_USB_PATH=1-2.3 npm run test:hardware   # optional: COMCONTROL_HW_MAX_RPM=1500
```

It expects a fan on fan connector 1 and a sensor on temperature connector 1. It checks that temperature and a fan speed above 0 RPM are visible in the UI at the same time, not simulated, updated every 5 seconds, and that fixed power and a hardware curve change the measured speed. It writes fixed power and a curve to fan 1 and restores 100 % fixed power at the end. Observations are written to `test-results/hardware/observations.json`.

Steps that need physical action (warming the sensor, unplugging USB or the sensor, blocking a fan) are not automated and must be checked by hand.
