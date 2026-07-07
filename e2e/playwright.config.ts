import { defineConfig } from '@playwright/test';
import * as path from 'path';
import * as dotenv from 'dotenv';

dotenv.config({ path: path.join(__dirname, '.env') });

// Suite E2E contro gli ambienti deployati (Railway). Un solo worker e ordine
// alfabetico dei file: i test condividono lo stato del merchant e2e-playwright
// (config bot, knowledge base), quindi non possono girare in parallelo.
export default defineConfig({
  testDir: './tests',
  timeout: 300_000,
  workers: 1,
  fullyParallel: false,
  retries: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 20_000,
    navigationTimeout: 60_000,
    launchOptions: {
      // Osservato: fetch POST /playground/turn appesi >180s su connessioni h2
      // riusate verso l'edge Railway (curl con connessione fresca risponde in ~10s).
      // Forzare HTTP/1.1 evita il riuso di connessioni h2 stantie.
      args: ['--disable-http2'],
    },
  },
});
