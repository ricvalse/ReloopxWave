import { test, expect } from '@playwright/test';
import { loginAdmin } from '../helpers/auth';
import { ADMIN_URL, MERCHANT_NAME } from '../helpers/env';
import { sendPlaygroundTurn } from '../helpers/playground';

// L'admin agenzia entra nel portale merchant via token di impersonation
// (nuovo tab su /impersonate#token=…) e il playground funziona con quel token.
test('impersonation: "Entra come merchant" apre il portale e il playground risponde', async ({ page, context }) => {
  await loginAdmin(page);
  await page.goto(`${ADMIN_URL}/merchants`);
  await page
    .locator('tr', { hasText: MERCHANT_NAME })
    .first()
    .getByRole('link', { name: /Dettagli/ })
    .click();
  await page.waitForURL(/\/merchants\/[0-9a-f-]+/, { timeout: 30_000 });

  // Il popup si apre solo dopo il POST /admin/impersonation/{id}: sotto latenza può superare i 30s.
  const popupPromise = context.waitForEvent('page', { timeout: 90_000 });
  await page.getByRole('button', { name: 'Entra come merchant' }).click();
  const merchantPage = await popupPromise;

  await merchantPage.waitForURL(/\/dashboard/, { timeout: 60_000 });

  await merchantPage.goto(merchantPage.url().replace(/\/dashboard.*/, '/bot/playground'));
  await expect(merchantPage.getByPlaceholder('Scrivi…')).toBeVisible({ timeout: 30_000 });
  const turn = await sendPlaygroundTurn(merchantPage, 'Ciao! Siete aperti domani pomeriggio?');
  expect(turn.reply_text.length).toBeGreaterThan(0);
});
