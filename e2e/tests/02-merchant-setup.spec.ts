import { test, expect } from '@playwright/test';
import { loginAdmin } from '../helpers/auth';
import { ADMIN_URL, MERCHANT_NAME, MERCHANT_SLUG, MERCHANT_EMAIL, MERCHANT_PASSWORD } from '../helpers/env';

// Setup idempotente: crea il merchant dedicato ai test E2E e il suo utente
// merchant_user (con password, senza email) se non esistono già.
// Passa dalla UI admin, così il flusso di creazione è esso stesso sotto test.
test('crea (o riusa) il merchant E2E e il suo utente', async ({ page }) => {
  await loginAdmin(page);
  await page.goto(`${ADMIN_URL}/merchants`);
  await expect(page.getByRole('button', { name: /Nuovo merchant/ })).toBeVisible({ timeout: 30_000 });

  const merchantEntry = page.getByText(MERCHANT_NAME, { exact: true }).first();
  if (!(await merchantEntry.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: /Nuovo merchant/ }).click();
    await page.fill('#slug', MERCHANT_SLUG);
    await page.fill('#name', MERCHANT_NAME);
    await page.getByRole('button', { name: 'Crea merchant' }).click();
    await expect(page.getByText(MERCHANT_NAME, { exact: true }).first()).toBeVisible({ timeout: 20_000 });
  }

  // Apri il dettaglio merchant: la riga non è cliccabile, si passa dal link "Dettagli →".
  await page
    .locator('tr', { hasText: MERCHANT_NAME })
    .first()
    .getByRole('link', { name: /Dettagli/ })
    .click();
  await page.waitForURL(/\/merchants\/[0-9a-f-]+/, { timeout: 30_000 });
  await expect(page.getByText('Utenti merchant')).toBeVisible({ timeout: 30_000 });
  // Attendi che la lista utenti abbia finito di caricare prima di decidere se creare.
  await expect(page.getByText('Caricamento utenti…')).toBeHidden({ timeout: 30_000 });

  const userListed = await page.getByText(MERCHANT_EMAIL).first().isVisible().catch(() => false);
  if (!userListed) {
    await page.getByRole('button', { name: /Crea utente/ }).click();
    await page.fill('#invite-email', MERCHANT_EMAIL);
    await page.fill('#invite-password', MERCHANT_PASSWORD);
    await page.getByRole('button', { name: 'Crea utente' }).click();
    await expect(page.getByText(/Utente creato/)).toBeVisible({ timeout: 20_000 });
  }
});
