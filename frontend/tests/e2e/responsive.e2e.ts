import { expect, test } from '@playwright/test';
import { setLanguage } from './helpers.ts';

// Runs in the "mobile-dark" project (narrow viewport, dark system scheme) and
// on desktop. Runs after 03-settings changed the password.
const PASSWORD = 'e2e-secret-1';

test('narrow view and system dark mode', async ({ page }, info) => {
	await page.goto('/login');
	await page.locator('#password').fill(PASSWORD);
	await page.locator('form button[type=submit]').click();
	await expect(page).toHaveURL(/\/$/);
	await setLanguage(page, 'en');
	if (info.project.name === 'mobile-dark') {
		await expect(page.locator('html')).toHaveClass(/dark/);
	}
	await expect(page.getByTestId('sensor-temp1').getByTestId('temp-value')).toHaveText(/°C/);
	// no horizontal overflow on any page
	for (const path of ['/', '/channels', '/curves', '/history', '/alarms', '/settings']) {
		await page.goto(path);
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
		const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
		expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(1);
		await page.screenshot({ path: `test-results/screens/${info.project.name}${path === '/' ? '/dashboard' : path}.png`, fullPage: true });
	}
});
