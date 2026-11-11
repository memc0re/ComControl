import { expect, test } from '@playwright/test';
import { login, setLanguage } from './helpers.ts';

test('disclaimer and limits are shown in both languages', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'de');
	await page.goto('/settings');
	await expect(page.getByTestId('disclaimer')).toContainText('Nutzung auf eigene Gefahr');
	await expect(page.getByText('Es gibt keinen unabhängigen Wächter')).toBeVisible();
	await setLanguage(page, 'en');
	await expect(page.getByTestId('disclaimer')).toContainText('Use at your own risk');
});

test('password change and login with the new password', async ({ page }) => {
	await login(page);
	await setLanguage(page, 'en');
	await expect(page.getByText('The default password is still active')).toBeVisible();
	await page.goto('/settings');
	await page.locator('#pw-current').fill('admin');
	await page.locator('#pw-new').fill('e2e-secret-1');
	await page.locator('#pw-repeat').fill('e2e-secret-2');
	await page.getByRole('button', { name: 'Change password' }).click();
	await expect(page.getByTestId('error-alert')).toContainText('do not match');
	await page.locator('#pw-repeat').fill('e2e-secret-1');
	await page.getByRole('button', { name: 'Change password' }).click();
	await expect(page.getByText('Password changed')).toBeVisible();
	await expect(page.getByText('The default password is still active')).toBeHidden();
	await page.getByTestId('logout').click();
	await expect(page).toHaveURL(/\/login$/);
	await login(page, 'e2e-secret-1');
});
