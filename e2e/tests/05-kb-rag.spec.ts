import { test, expect } from '@playwright/test';
import { loginMerchant } from '../helpers/auth';
import { setRagMinScore } from '../helpers/api';
import { MERCHANT_URL } from '../helpers/env';
import { openPlayground, resetPlayground, sendPlaygroundTurn } from '../helpers/playground';

// UC-07: il RAG sulla knowledge base deve fondare le risposte del bot.
// Fatti distintivi inventati: prima dell'upload il bot non li conosce
// (0 chunk recuperati), dopo l'indicizzazione li recupera e li usa.
const KB_TITLE = 'Listino E2E Onda';
const DOMANDA = 'Cosa include il pacchetto Onda Zaffiro e quanto costa esattamente?';
const PREZZO = '149';

const DOC_TEXT = `LISTINO SERVIZI — PACCHETTI SPECIALI (documento di test E2E)

Pacchetto Onda Zaffiro
Il pacchetto Onda Zaffiro è il nostro trattamento premium esclusivo.
Prezzo: 149 euro, IVA inclusa.
Include: consulenza personalizzata iniziale, trattamento completo di 90 minuti,
un prodotto omaggio della linea Zaffiro e un follow-up telefonico dopo 7 giorni.
Disponibile solo su appuntamento, dal martedì al sabato.
Con il codice promozionale ZAFFIRO10 si ottiene il 10% di sconto sul pacchetto Onda Zaffiro.

Pacchetto Onda Corallo
Il pacchetto Onda Corallo costa 79 euro e include un trattamento base di 45 minuti.
È pensato per chi vuole provare i nostri servizi per la prima volta.

Politica di cancellazione dei pacchetti Onda
Le prenotazioni dei pacchetti Onda possono essere annullate gratuitamente fino a 24 ore prima
dell'appuntamento. Oltre questo termine viene trattenuto il 30% del prezzo del pacchetto.
`;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  // Soglia RAG raggiungibile dalla query grezza (vedi helpers/api.ts per il perché).
  await setRagMinScore(0.55);
});

test.beforeEach(async ({ page }) => {
  await loginMerchant(page);
});

test('senza KB: il RAG non recupera nulla e il bot non conosce il prezzo', async ({ page }) => {
  // Idempotenza: rimuovi i documenti E2E residui di run precedenti.
  page.on('dialog', (d) => void d.accept());
  await page.goto(`${MERCHANT_URL}/bot/knowledge-base`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByText('Carica documento')).toBeVisible({ timeout: 30_000 });
  // Attendi che la lista documenti abbia caricato (righe con header "Titolo" o empty state).
  await expect(page.getByText(/Nessun documento|Titolo/).first()).toBeVisible({ timeout: 30_000 });
  for (let i = 0; i < 5; i += 1) {
    const row = page.locator('tr', { hasText: KB_TITLE }).first();
    if (!(await row.isVisible().catch(() => false))) break;
    await row.locator('button').last().click(); // ultimo bottone riga = elimina (Trash2)
    await expect(row).toBeHidden({ timeout: 20_000 });
  }

  await openPlayground(page);
  await resetPlayground(page);
  const turn = await sendPlaygroundTurn(page, DOMANDA);
  expect(turn.retrieved_chunks, `attesi 0 chunk, ricevuti: ${JSON.stringify(turn.retrieved_chunks)}`).toHaveLength(0);
  expect(turn.reply_text, `il bot non deve conoscere il prezzo senza KB: "${turn.reply_text}"`).not.toContain(PREZZO);
});

test('upload documento → indicizzazione automatica', async ({ page }) => {
  await page.goto(`${MERCHANT_URL}/bot/knowledge-base`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByText('Carica documento')).toBeVisible({ timeout: 30_000 });

  // Prova d'idratazione DETERMINISTICA: il toggle File/URL è puro stato React,
  // quindi finché non commuta la UI gli handler non sono attivi. Ripeti fino a
  // quando il click su "URL" fa comparire l'input URL — a quel punto React è vivo
  // e onChange del file input funziona (setInputFiles prima dell'idratazione
  // perderebbe l'evento e il file non verrebbe mai impostato).
  await expect(async () => {
    await page.getByRole('button', { name: 'URL', exact: true }).click();
    await expect(page.getByPlaceholder('https://esempio.it/pagina')).toBeVisible({ timeout: 1_500 });
  }).toPass({ timeout: 45_000 });
  await page.getByRole('button', { name: 'File', exact: true }).click();
  await expect(page.locator('input[type="file"]')).toBeAttached();

  await page.getByPlaceholder(/Titolo/).fill(KB_TITLE);
  await page.locator('input[type="file"]').setInputFiles({
    name: 'listino-e2e-onda.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from(DOC_TEXT, 'utf-8'),
  });
  // La label mostra il nome file = stato React del file impostato.
  await expect(page.getByText('listino-e2e-onda.txt')).toBeVisible({ timeout: 10_000 });

  const submit = page.getByRole('button', { name: 'Carica e indicizza' });
  await expect(submit).toBeEnabled({ timeout: 10_000 });
  await submit.click();

  // Upload diretto a Supabase Storage + POST /docs + refetch lista: sotto latenza
  // infra può superare i 30s.
  await expect(page.locator('tr', { hasText: KB_TITLE }).first()).toBeVisible({ timeout: 90_000 });

  // BUG PIATTAFORMA (verificato 2026-07-07): la lista KB NON fa auto-poll.
  // kb-doc-list.tsx usa `refetchInterval: (data) => Array.isArray(data) && …` ma
  // sotto TanStack Query v5 quel callback riceve il Query, non i dati →
  // Array.isArray(query) è sempre false → polling mai attivo. Il worker ARQ
  // indicizza in ~1,5s (verificato nei log + DB) ma la UI resta "pending" finché
  // l'utente non ricarica. Qui ricarichiamo noi per osservare il flip a "indexed".
  await expect(async () => {
    await page.reload({ waitUntil: 'domcontentloaded' });
    const r = page.locator('tr', { hasText: KB_TITLE }).first();
    await expect(r).toContainText('indexed', { timeout: 5_000 });
  }).toPass({ timeout: 180_000 });
  await expect(page.locator('tr', { hasText: KB_TITLE }).first()).not.toContainText('failed');
});

test('con KB indicizzata: chunk recuperati e risposta fondata sul documento', async ({ page }) => {
  await openPlayground(page);
  await resetPlayground(page);
  const turn = await sendPlaygroundTurn(page, DOMANDA);

  expect(turn.retrieved_chunks.length, 'attesi chunk KB recuperati dal RAG').toBeGreaterThan(0);
  expect(turn.reply_text, `risposta non fondata sul listino: "${turn.reply_text}"`).toContain(PREZZO);

  // Il pannello "Dettagli tecnici" deve esporre i chunk recuperati.
  await expect(page.getByText(/Knowledge base \([1-9]\d*\)/)).toBeVisible({ timeout: 30_000 });
});

test('domanda fuori contesto: il RAG non recupera chunk non pertinenti', async ({ page }) => {
  await openPlayground(page);
  await resetPlayground(page);
  const turn = await sendPlaygroundTurn(page, 'Che tempo farà domani a Milano?');
  expect(turn.retrieved_chunks, `domanda meteo non deve matchare il listino: ${JSON.stringify(turn.retrieved_chunks.map((c) => c.score))}`).toHaveLength(0);
});
