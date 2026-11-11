import { expect, type Page } from '@playwright/test';

export async function login(page: Page, password = 'admin') {
	await page.goto('/login');
	await page.locator('#username').fill('admin');
	await page.locator('#password').fill(password);
	await page.locator('form button[type=submit]').click();
	await expect(page).toHaveURL(/\/$/);
}

export async function setLanguage(page: Page, lang: 'de' | 'en') {
	await page.getByTestId('lang-switch').selectOption(lang);
}

/** Fault injection via the simulator endpoint (exists only in simulation mode). */
export async function simulate(page: Page, body: Record<string, unknown>) {
	const session = await page.request.get('/api/v1/auth/session');
	const { csrf_token } = await session.json();
	const res = await page.request.post('/api/v1/simulator', {
		data: body,
		headers: { 'X-CSRF-Token': csrf_token }
	});
	expect(res.status()).toBe(204);
}
