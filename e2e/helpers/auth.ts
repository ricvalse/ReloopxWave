import { Page, expect } from '@playwright/test';
import { ADMIN_URL, ADMIN_EMAIL, ADMIN_PASSWORD, MERCHANT_URL, MERCHANT_EMAIL, MERCHANT_PASSWORD } from './env';

async function login(page: Page, baseUrl: string, email: string, password: string) {
  await page.goto(`${baseUrl}/login`);
  await page.fill('#email', email);
  await page.fill('#password', password);
  await page.getByRole('button', { name: 'Accedi' }).click();
  await page.waitForURL('**/dashboard**', { timeout: 60_000 });
  await expect(page).not.toHaveURL(/\/login/);
}

export async function loginAdmin(page: Page) {
  await login(page, ADMIN_URL, ADMIN_EMAIL, ADMIN_PASSWORD);
}

export async function loginMerchant(page: Page) {
  await login(page, MERCHANT_URL, MERCHANT_EMAIL, MERCHANT_PASSWORD);
}
