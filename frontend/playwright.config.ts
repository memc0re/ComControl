import { defineConfig, devices } from '@playwright/test';

// End-to-end tests run the real backend in simulation mode (preset "gaps")
// with a fresh temporary database, serving the built UI. No real hardware.
const PORT = 4180;

export default defineConfig({
	testDir: 'tests/e2e',
	testMatch: '**/*.e2e.ts',
	fullyParallel: false,
	workers: 1,
	retries: 0,
	timeout: 60_000,
	expect: { timeout: 15_000 },
	reporter: [['list']],
	use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
	webServer: {
		command: `node tests/e2e/server.mjs ${PORT}`,
		url: `http://127.0.0.1:${PORT}/api/v1/health`,
		reuseExistingServer: false,
		timeout: 60_000
	},
	projects: [
		{ name: 'desktop', use: { ...devices['Desktop Chrome'], colorScheme: 'light' } },
		{
			name: 'mobile-dark',
			use: { ...devices['Pixel 7'], colorScheme: 'dark' },
			dependencies: ['desktop'],
			testMatch: '**/responsive.e2e.ts'
		}
	]
});
