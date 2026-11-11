import { defineConfig, devices } from '@playwright/test';

// Hardware acceptance test (SPEC 9.2) against the REAL Commander PRO.
// Explicitly selected only:  COMCONTROL_HW_USB_PATH=1-2.3 npm run test:hardware
// Uses a fresh temporary database; never a productive installation. It writes
// fixed power and a curve to fan 1 and restores 100 % fixed power at the end.
// Alternatively COMCONTROL_HW_BASE_URL tests an already running instance with a
// fresh database (e.g. the container), instead of starting the backend.
const PORT = 4181;
const external = process.env.COMCONTROL_HW_BASE_URL;
if (!process.env.COMCONTROL_HW_USB_PATH && !external) {
	throw new Error('set COMCONTROL_HW_USB_PATH to the USB port path of the controller under test');
}

export default defineConfig({
	testDir: 'tests/hardware',
	testMatch: '**/*.hw.ts',
	workers: 1,
	retries: 0,
	timeout: 180_000,
	expect: { timeout: 30_000 },
	reporter: [['list']],
	use: { ...devices['Desktop Chrome'], baseURL: external ?? `http://127.0.0.1:${PORT}` },
	webServer: external
		? undefined
		: {
				command: `node tests/e2e/server.mjs ${PORT}`,
				url: `http://127.0.0.1:${PORT}/api/v1/health`,
				reuseExistingServer: false,
				timeout: 60_000
			}
});
