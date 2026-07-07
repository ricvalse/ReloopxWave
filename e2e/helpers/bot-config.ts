import { Page, expect } from '@playwright/test';
import { MERCHANT_URL } from './env';

export async function openBotConfig(page: Page) {
  await page.goto(`${MERCHANT_URL}/bot/config`, { waitUntil: 'domcontentloaded' });
  // Il pannello è pronto quando compare un campo della sezione "Bot — Persona".
  await expect(page.locator('[id="bot.language"]')).toBeVisible({ timeout: 30_000 });
}

/** Imposta un campo del pannello config (input/textarea/select individuati da id = chiave puntata). */
export async function setConfigField(page: Page, key: string, value: string) {
  const el = page.locator(`[id="${key}"]`);
  await el.scrollIntoViewIfNeeded();
  const tag = await el.evaluate((e) => e.tagName);
  if (tag === 'SELECT') {
    await el.selectOption(value);
  } else {
    await el.fill(value);
  }
}

/** Salva se ci sono modifiche pendenti; no-op altrimenti. Attende il PUT overrides. */
export async function saveConfig(page: Page) {
  const btn = page.getByRole('button', { name: /Salva \(\d+\)/ });
  if (!(await btn.isVisible().catch(() => false))) return;
  // "Salva (0)" disabilitato = nessuna modifica pendente: niente da salvare.
  if (!(await btn.isEnabled().catch(() => false))) return;
  const respPromise = page.waitForResponse(
    (r) => r.url().includes('/overrides') && r.request().method() === 'PUT',
    { timeout: 30_000 },
  );
  await btn.click();
  const resp = await respPromise;
  expect(resp.ok(), `PUT overrides ha risposto ${resp.status()}`).toBeTruthy();
  // Il bottone torna disabilitato (dirty=0) a salvataggio recepito.
  await expect(page.getByRole('button', { name: /Salva/ })).toBeDisabled({ timeout: 15_000 });
}
