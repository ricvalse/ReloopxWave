export const ADMIN_URL = process.env.E2E_ADMIN_URL ?? 'https://admin.wave.relooptech.ai';
export const MERCHANT_URL = process.env.E2E_MERCHANT_URL ?? 'https://wave.relooptech.ai';
export const API_URL = process.env.E2E_API_URL ?? 'https://api-production-6ac7.up.railway.app';

export const ADMIN_EMAIL = required('E2E_ADMIN_EMAIL');
export const ADMIN_PASSWORD = required('E2E_ADMIN_PASSWORD');

// Merchant dedicato ai test: creato (se manca) dallo spec 02 via UI admin.
// Non toccare i merchant reali del tenant.
export const MERCHANT_NAME = 'E2E Playwright';
export const MERCHANT_SLUG = 'e2e-playwright';
export const MERCHANT_EMAIL = required('E2E_MERCHANT_EMAIL');
export const MERCHANT_PASSWORD = required('E2E_MERCHANT_PASSWORD');

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Variabile d'ambiente mancante: ${name} (vedi e2e/.env.example)`);
  return v;
}
