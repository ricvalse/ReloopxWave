import { Page, expect } from '@playwright/test';
import { MERCHANT_URL } from './env';

export interface PlaygroundTurn {
  reply_text: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  retrieved_chunks: { chunk_id: string; score: number; snippet: string }[];
  bubbles: { text: string; delay_ms: number }[];
  typing_indicator: boolean;
  events: { kind: string; summary: string; detail?: unknown }[];
  state: Record<string, unknown>;
}

export async function openPlayground(page: Page) {
  // domcontentloaded: il "load" completo può superare i 60s nei momenti di lentezza
  // infra; l'attesa vera è sull'input del playground.
  await page.goto(`${MERCHANT_URL}/bot/playground`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByPlaceholder('Scrivi…')).toBeVisible({ timeout: 30_000 });
}

export async function resetPlayground(page: Page) {
  const btn = page.getByRole('button', { name: 'Pulisci' });
  if (await btn.isVisible().catch(() => false)) {
    if (await btn.isEnabled().catch(() => false)) await btn.click();
  }
}

/**
 * Invia un messaggio nel playground e restituisce il JSON di POST /playground/turn.
 * Asserire sulla risposta di rete è molto più stabile che leggere le bolle in UI
 * (che arrivano con ritardi di digitazione simulati).
 */
export async function sendPlaygroundTurn(page: Page, message: string): Promise<PlaygroundTurn> {
  // Osservato in produzione: sporadicamente POST /playground/turn resta appeso
  // >3 minuti (nessun timeout server-side). Al timeout ricarichiamo e ritentiamo
  // una volta: il turno appeso non altera lo stato (il playground è stateless
  // lato server, la history vive nel client).
  let lastErr: unknown;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const input = page.getByPlaceholder('Scrivi…');
      const sendBtn = input.locator('xpath=following-sibling::button[1]');
      // Guardia di idratazione: l'input è visibile già dal SSR ma i handler React
      // si attaccano dopo. Il bottone invio resta disabled finché React non vede
      // il testo: ri-digita finché non si abilita, solo allora Invio ha effetto.
      await expect(async () => {
        await input.fill(message);
        await expect(sendBtn).toBeEnabled({ timeout: 1_500 });
      }).toPass({ timeout: 45_000 });
      const respPromise = page.waitForResponse(
        (r) => r.url().includes('/playground/turn') && r.request().method() === 'POST',
        { timeout: 120_000 },
      );
      await input.press('Enter');
      const resp = await respPromise;
      expect(resp.ok(), `POST /playground/turn ha risposto ${resp.status()}: ${await resp.text().catch(() => '')}`).toBeTruthy();
      const json = (await resp.json()) as PlaygroundTurn;
      expect(json.reply_text, 'reply_text vuoto').toBeTruthy();
      return json;
    } catch (err) {
      lastErr = err;
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.getByPlaceholder('Scrivi…')).toBeVisible({ timeout: 30_000 });
    }
  }
  throw lastErr;
}

/**
 * Punteggio grezzo di "italianità" vs "inglesità" di un testo (stopword count).
 * Solo token NON ambigui tra le due lingue: es. "a" è escluso perché è sia
 * articolo inglese sia preposizione italiana frequentissima (falsi positivi EN).
 */
export function languageScores(text: string): { it: number; en: number } {
  const words = text.toLowerCase().split(/[^a-zàèéìòù']+/).filter(Boolean);
  const IT = new Set(['il', 'lo', 'gli', 'una', 'che', 'per', 'con', 'sono', 'siamo', 'della', 'nostro', 'nostra', 'grazie', 'ciao', 'posso', 'puoi', 'più', 'anche', 'come', 'cosa', 'nel', 'alla', 'ecco', 'certo', 'volentieri', 'aiutarti', 'aiutarla', 'disposizione', 'prenotare', 'appuntamento']);
  const EN = new Set(['the', 'and', 'you', 'your', 'are', 'can', 'help', 'with', 'hello', 'thanks', 'please', 'would', 'like', 'about', 'what', 'how', 'book', 'booking', 'appointment', 'service', 'available']);
  let it = 0;
  let en = 0;
  for (const w of words) {
    if (IT.has(w)) it += 1;
    if (EN.has(w)) en += 1;
  }
  return { it, en };
}
