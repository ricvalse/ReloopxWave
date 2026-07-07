import { test, expect } from '@playwright/test';
import { loginMerchant } from '../helpers/auth';
import { openPlayground } from '../helpers/playground';

test('login merchant → dashboard e playground raggiungibile', async ({ page }) => {
  await loginMerchant(page);
  await expect(page).toHaveURL(/\/dashboard/);
  await openPlayground(page);
  await expect(page.getByPlaceholder('Scrivi…')).toBeVisible();
});
