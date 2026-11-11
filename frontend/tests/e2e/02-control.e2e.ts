import { expect, test } from '@playwright/test';
import { login, setLanguage, simulate } from './helpers.ts';

test.describe.configure({ mode: 'serial' });

test('set up channels: name, activate, alarm limit', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'en');
	await page.goto('/channels');
	const fan = page.getByTestId('ch-fan1');
	await fan.getByLabel('Name').fill('Front');
	await fan.getByLabel('Usage').selectOption('expected');
	await fan.getByLabel('Maximum speed (RPM)').fill('1500');
	await fan.getByRole('button', { name: 'Save' }).click();
	await expect(fan.getByRole('status')).toContainText('Activated: 100 % transmitted.');

	const sensor = page.getByTestId('ch-temp1');
	await sensor.getByLabel('Name').fill('Inlet');
	await sensor.getByLabel('Usage').selectOption('expected');
	await sensor.getByLabel('Alarm above (°C)').fill('45');
	await sensor.getByRole('button', { name: 'Save' }).click();
	await expect(sensor.getByRole('status')).toContainText('Saved.');

	await page.goto('/');
	await expect(page.getByTestId('fan-fan1')).toContainText('Front (fan1)');
	await expect(page.getByTestId('fan-fan1').getByTestId('fan-desired')).toContainText('100 % fixed');
	await expect(page.getByTestId('fan-fan1').getByTestId('fan-sent')).toContainText('transmitted');
	await expect(page.getByTestId('sensor-temp1')).toContainText('Alarm above 45.0 °C');
});

test('stale revision from another window gives a conflict', async ({ page, browser }) => {
	await login(page);
	await setLanguage(page, 'en');
	await page.goto('/channels');
	const other = await browser.newPage();
	await login(other);
	await other.goto('/channels');
	await other.getByTestId('ch-temp2').getByLabel('Name').fill('Other window');
	await other.getByTestId('ch-temp2').getByRole('button', { name: /Save|Speichern/ }).click();
	await expect(other.getByTestId('ch-temp2').getByRole('status')).toBeVisible();
	await other.close();
	await page.getByTestId('ch-temp3').getByLabel('Name').fill('Stale');
	await page.getByTestId('ch-temp3').getByRole('button', { name: 'Save' }).click();
	await expect(page.getByTestId('ch-temp3').getByTestId('error-alert')).toContainText('changed in the meantime');
});

test('fixed power and curve editing', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'en');
	await page.goto('/curves');
	await page.locator('#percent').fill('40');
	await page.getByTestId('apply').click();
	await expect(page.getByTestId('apply-ok')).toBeVisible();
	await page.goto('/');
	await expect(page.getByTestId('fan-fan1').getByTestId('fan-desired')).toContainText('40 % fixed');
	await expect(page.getByTestId('fan-fan1').getByTestId('rpm-value')).toHaveText(/^(58[5-9]|59\d|6[01]\d) RPM$/, { timeout: 12_000 }) // 600 ± simulator jitter;

	await page.goto('/curves');
	await page.getByLabel('Hardware curve').check();
	await page.locator('#sensor').selectOption('temp1');
	await page.locator('#pt-0-t').fill('20');
	await page.locator('#pt-0-r').fill('500');
	await page.locator('#pt-1-t').fill('40');
	await page.locator('#pt-1-r').fill('1500');
	await expect(page.getByTestId('effective-curve').locator('tbody tr')).toHaveCount(6);
	// invalid curve: last point below the maximum speed is rejected with a reason
	await page.locator('#pt-1-r').fill('1400');
	await expect(page.getByTestId('error-alert').first()).toContainText('maximum speed (1500 RPM)');
	await page.locator('#pt-1-r').fill('1500');
	await expect(page.getByTestId('effective-curve')).toBeVisible();
	await page.getByTestId('apply').click();
	await expect(page.getByTestId('apply-ok')).toBeVisible();
	await page.goto('/');
	await expect(page.getByTestId('fan-fan1').getByTestId('fan-desired')).toContainText('Curve on temp1');
});

test('fail-safe on over-temperature and explicit release', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'en');
	await simulate(page, { sensor_temperatures: { temp1: 60 } });
	await expect(page.getByTestId('failsafe-banner')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('fan-fan1').getByTestId('fan-failsafe')).toContainText('over-temperature');
	await expect(page.getByTestId('nav-alarm-count')).toBeVisible();

	// profile changes are blocked while latched
	await page.goto('/curves');
	await expect(page.getByTestId('ctl-locked')).toBeVisible();
	await expect(page.getByTestId('apply')).toBeDisabled();

	await page.goto('/alarms');
	await expect(page.getByTestId('active-alarms')).toContainText('Over-temperature');
	await page.getByRole('button', { name: 'Acknowledge' }).click();
	await expect(page.getByTestId('active-alarms')).toContainText('Acknowledged');
	// release refused while the cause is active
	await page.getByTestId('release').click();
	await expect(page.getByTestId('error-alert').first()).toContainText('still active');

	// cause ends: alarm ends, latch stays until released
	await simulate(page, { sensor_temperatures: { temp1: 25 } });
	await expect(page.getByTestId('no-active-alarms')).toBeVisible({ timeout: 15_000 });
	await expect(page.getByTestId('failsafe-list')).toBeVisible();
	await page.getByTestId('release').click();
	await expect(page.getByTestId('release-msg')).toContainText('Released: fan1');
	await expect(page.getByTestId('failsafe-none')).toBeVisible();
	await expect(page.getByTestId('failsafe-banner')).toBeHidden();
});

test('controller loss: error state and fail-safe after reconnect', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'en');
	await simulate(page, { unplugged: true });
	await expect(page.getByTestId('connection')).toContainText('Disconnected', { timeout: 15_000 });
	await expect(page.getByTestId('connection-error')).toContainText('Communication with the controller failed');
	await expect(page.getByTestId('sensor-temp1')).toContainText('Stale', { timeout: 25_000 });
	await expect(page.getByTestId('fan-fan1').getByTestId('fan-failsafe')).toHaveCount(0);
	await simulate(page, { unplugged: false });
	await expect(page.getByTestId('connection')).toContainText('Connected', { timeout: 15_000 });
	await expect(page.getByTestId('fan-fan1').getByTestId('fan-failsafe')).toContainText('controller reconnected');
	await page.goto('/alarms');
	await page.getByTestId('release').click();
	await expect(page.getByTestId('failsafe-none')).toBeVisible();
});
