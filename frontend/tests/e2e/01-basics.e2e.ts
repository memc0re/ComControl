import { expect, test } from '@playwright/test';
import { login, setLanguage } from './helpers.ts';

test('redirects to login and rejects wrong password', async ({ page }) => {
	await page.goto('/');
	await expect(page).toHaveURL(/\/login$/);
	await setLanguage(page, 'en');
	await page.locator('#password').fill('wrong');
	await page.locator('form button[type=submit]').click();
	await expect(page.getByTestId('error-alert')).toContainText('User name or password is wrong');
});

test('dashboard shows live simulated values that update', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'en');
	await expect(page.getByTestId('simulation-banner')).toBeVisible();
	await expect(page.getByTestId('connection')).toContainText('Connected');
	// detected but unused channels are visible with live values
	const temp = page.getByTestId('sensor-temp1').getByTestId('temp-value');
	await expect(temp).toHaveText(/\d+\.\d °C/);
	await expect(page.getByTestId('fan-fan1').getByTestId('rpm-value')).toHaveText(/[1-9][\d,]* RPM/);
	// not connected channels are gaps, not zero
	await page.getByLabel('Show unused channels').check();
	await expect(page.getByTestId('sensor-temp2').getByTestId('temp-value')).toHaveText('–');
	await expect(page.getByTestId('sensor-temp2')).toContainText('Not connected');
	// the data age is refreshed by the 5 s polling
	const age = page.getByTestId('data-age');
	await expect(age).toHaveText(/\d+ s/);
	await expect(page.getByTestId('no-channels')).toBeVisible();
});

test('language and theme switches', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'de');
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('Übersicht');
	await expect(page.locator('html')).toHaveAttribute('lang', 'de');
	await setLanguage(page, 'en');
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('Dashboard');
	const dark = await page.locator('html').evaluate((el) => el.classList.contains('dark'));
	await page.getByTestId('theme-toggle').click();
	await expect(page.locator('html')).toHaveClass(dark ? /^(?!.*dark)/ : /dark/);
	// preference survives a reload (language and theme only; no credentials in storage)
	await page.reload();
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('Dashboard');
	const keys = await page.evaluate(() => Object.keys(localStorage));
	expect(keys.sort()).toEqual(['comcontrol.lang', 'comcontrol.theme']);
});

test('unknown routes are real errors, not the app', async ({ request }) => {
	const api = await request.get('/api/v1/nothing');
	expect(api.status()).toBe(404);
	expect(api.headers()['content-type']).toContain('application/json');
	const asset = await request.get('/_app/missing.js');
	expect(asset.status()).toBe(404);
	const page = await request.get('/does-not-exist');
	expect(page.status()).toBe(404);
	const known = await request.get('/history');
	expect(known.status()).toBe(200);
});

test('backend loss is shown', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'en');
	await page.route('**/api/v1/state', (route) => route.abort());
	await expect(page.getByTestId('backend-lost')).toBeVisible({ timeout: 12_000 });
	await page.unroute('**/api/v1/state');
	await expect(page.getByTestId('backend-lost')).toBeHidden({ timeout: 12_000 });
});
