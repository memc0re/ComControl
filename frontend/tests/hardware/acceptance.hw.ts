// Hardware acceptance (SPEC 9.2) with the real controller: live temperature and
// fan speed in the UI, 5 s updates, not simulated, speed changes after fixed
// power and curve changes. Assumes a fan on fan 1 and a sensor on temp 1.
// Observations are written to test-results/hardware/observations.json.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';

const FAN = 'fan1';
const SENSOR = 'temp1';
const MAX_RPM = Number(process.env.COMCONTROL_HW_MAX_RPM ?? 1500);
const log: Record<string, unknown>[] = [];

function note(step: string, data: Record<string, unknown>) {
	log.push({ at: new Date().toISOString(), step, ...data });
	console.log(step, JSON.stringify(data));
}

async function uiValues(page: Page) {
	const temp = await page.getByTestId(`sensor-${SENSOR}`).getByTestId('temp-value').innerText();
	const rpm = await page.getByTestId(`fan-${FAN}`).getByTestId('rpm-value').innerText();
	return {
		temp,
		rpm,
		tempC: Number(temp.replace(/[^\d.,-]/g, '').replace(',', '.')),
		rpmN: Number(rpm.replace(/[^\d]/g, ''))
	};
}

async function apply(page: Page, fill: () => Promise<void>) {
	await page.goto('/curves');
	await fill();
	await page.getByTestId('apply').click();
	await expect(page.getByTestId('apply-ok')).toBeVisible();
	await page.goto('/');
}

test.afterAll(() => {
	mkdirSync('test-results/hardware', { recursive: true });
	writeFileSync('test-results/hardware/observations.json', JSON.stringify(log, null, 2));
});

test('live measurement chain and control on the real controller', async ({ page }) => {
	await page.goto('/login');
	await page.getByTestId('lang-switch').selectOption('en');
	await page.locator('#password').fill('admin');
	await page.locator('form button[type=submit]').click();
	await expect(page).toHaveURL(/\/$/);

	// not simulated, connected
	await expect(page.getByTestId('connection')).toContainText('Connected');
	await expect(page.getByTestId('simulation-banner')).toHaveCount(0);
	const device = await (await page.request.get('/api/v1/device')).json();
	expect(device.simulated).toBe(false);
	note('device', { usb_path: device.usb_path, firmware: device.firmware, bootloader: device.bootloader });

	// temperature and rpm > 0 visible at the same time, refreshed every 5 s
	await expect(page.getByTestId(`fan-${FAN}`).getByTestId('rpm-value')).toHaveText(/[1-9][\d,]* RPM/);
	const samples: { t: number; measured: string; temp: string; rpm: string }[] = [];
	for (let i = 0; i < 5; i++) {
		const v = await uiValues(page);
		const state = await (await page.request.get('/api/v1/state')).json();
		const s = state.sensors.find((x: { id: string }) => x.id === SENSOR);
		expect(v.tempC).toBeGreaterThan(-20);
		expect(v.rpmN).toBeGreaterThan(0);
		samples.push({ t: Date.now(), measured: s.temperature.measured_at, temp: v.temp, rpm: v.rpm });
		await page.waitForTimeout(5_000);
	}
	const distinct = new Set(samples.map((s) => s.measured)).size;
	note('live samples (UI)', { samples, distinct_measurements: distinct });
	expect(distinct).toBeGreaterThanOrEqual(4);
	const ts = [...new Set(samples.map((s) => s.measured))].map((m) => Date.parse(m));
	const gaps = ts.slice(1).map((t, i) => (t - ts[i]) / 1000);
	note('poll intervals (s)', { gaps });
	for (const g of gaps) expect(g).toBeLessThan(6.5);

	// set up channels (activation sends 100 %)
	await page.goto('/channels');
	const fan = page.getByTestId(`ch-${FAN}`);
	await fan.getByLabel('Name').fill('Acceptance fan');
	await fan.getByLabel('Usage').selectOption('expected');
	await fan.getByLabel('Maximum speed (RPM)').fill(String(MAX_RPM));
	await fan.getByRole('button', { name: 'Save' }).click();
	await expect(fan.getByRole('status')).toContainText('Activated: 100 % transmitted.');
	const sensor = page.getByTestId(`ch-${SENSOR}`);
	await sensor.getByLabel('Usage').selectOption('expected');
	await sensor.getByLabel('Alarm above (°C)').fill('45');
	await sensor.getByRole('button', { name: 'Save' }).click();
	await expect(sensor.getByRole('status')).toContainText('Saved.');
	await page.goto('/');
	await expect.poll(async () => (await uiValues(page)).rpmN, { timeout: 40_000 }).toBeGreaterThan(MAX_RPM * 0.85);
	note('activation 100 %', await uiValues(page));

	// fixed 50 %: speed must drop visibly
	await apply(page, async () => {
		await page.getByLabel('Fixed power').check();
		await page.locator('#percent').fill('50');
	});
	await expect.poll(async () => (await uiValues(page)).rpmN, { timeout: 40_000 }).toBeLessThan(MAX_RPM * 0.7);
	await page.waitForTimeout(10_000);
	note('fixed 50 %', await uiValues(page));

	// hardware curve on temp1: expected speed from linear interpolation
	const points: [number, number][] = [[20, 600], [30, 1000], [40, MAX_RPM]];
	await apply(page, async () => {
		await page.getByLabel('Hardware curve').check();
		await page.locator('#sensor').selectOption(SENSOR);
		while ((await page.getByTestId('curve-point').count()) < points.length) {
			await page.getByRole('button', { name: 'Add point' }).click();
		}
		for (const [i, [t, r]] of points.entries()) {
			await page.locator(`#pt-${i}-t`).fill(String(t));
			await page.locator(`#pt-${i}-r`).fill(String(r));
		}
		await expect(page.getByTestId('effective-curve').locator('tbody tr')).toHaveCount(6);
	});
	const expected = (temp: number) => {
		if (temp <= points[0][0]) return points[0][1];
		for (let i = 1; i < points.length; i++) {
			const [t0, r0] = points[i - 1];
			const [t1, r1] = points[i];
			if (temp <= t1) return r0 + ((r1 - r0) * (temp - t0)) / (t1 - t0);
		}
		return points.at(-1)![1];
	};
	await expect
		.poll(async () => {
			const v = await uiValues(page);
			return Math.abs(v.rpmN - expected(v.tempC));
		}, { timeout: 60_000 })
		.toBeLessThan(120);
	const curveValues = await uiValues(page);
	note('curve', { ...curveValues, expected_rpm: Math.round(expected(curveValues.tempC)) });

	// restore 100 % fixed power
	await apply(page, async () => {
		await page.getByLabel('Fixed power').check();
		await page.locator('#percent').fill('100');
	});
	await expect.poll(async () => (await uiValues(page)).rpmN, { timeout: 40_000 }).toBeGreaterThan(MAX_RPM * 0.85);
	note('restored 100 %', await uiValues(page));

	const alarms = await (await page.request.get('/api/v1/alarms?state=all')).json();
	note('alarms during test', { alarms: alarms.alarms });
	expect(alarms.alarms).toEqual([]);
});
