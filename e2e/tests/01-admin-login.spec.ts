import { test, expect } from '@playwright/test';
import { loginAdmin } from '../helpers/auth';
import { ADMIN_URL } from '../helpers/env';

test('login admin → dashboard agenzia', async ({ page }) => {
  await loginAdmin(page);
  await expect(page).toHaveURL(/\/dashboard/);
  // La sidebar/nav deve esporre la voce Merchants (panel agenzia).
  await expect(page.getByRole('link', { name: /Merchant/i }).first()).toBeVisible({ timeout: 20_000 });
});

test('credenziali errate → resta sul login con errore', async ({ page }) => {
  await page.goto(`${ADMIN_URL}/login`);
  await page.fill('#email', 'admin@admin.com');
  await page.fill('#password', 'password-sbagliata');
  await page.getByRole('button', { name: 'Accedi' }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.locator('p.text-destructive')).toBeVisible({ timeout: 15_000 });
});
