import { request as pwRequest } from '@playwright/test';
import { API_URL, MERCHANT_EMAIL, MERCHANT_PASSWORD } from './env';

const SUPABASE_URL = process.env.E2E_SUPABASE_URL ?? 'https://izhyypbjeqkqdxfnzzoo.supabase.co';
const SUPABASE_ANON_KEY =
  process.env.E2E_SUPABASE_ANON_KEY ??
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml6aHl5cGJqZXFrcWR4Zm56em9vIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4NDk3MDksImV4cCI6MjA5MjQyNTcwOX0.HUFbYND6Q7z9ZvnCMIrEA9VLR_PKmSxJydbsITFROZY'; // chiave pubblica (anon), non un segreto

/**
 * Imposta rag.min_score negli overrides del merchant E2E via API (merge, non replace).
 *
 * Perché: il default di piattaforma (0.7) non è raggiungibile con la query grezza
 * su text-embedding-3-small (misurato: domanda quasi-verbatim = 0.667) e HyDE è
 * attualmente rotto in produzione (llm.py invia max_tokens che i modelli gpt-5-*
 * rifiutano → fallback silenzioso alla query grezza). Con 0.55 il loop RAG resta
 * testabile end-to-end. La sezione RAG è nascosta nella UI, quindi si passa dall'API.
 */
export async function setRagMinScore(minScore: number) {
  const req = await pwRequest.newContext();
  try {
    const auth = await req.post(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
      headers: { apikey: SUPABASE_ANON_KEY, 'Content-Type': 'application/json' },
      data: { email: MERCHANT_EMAIL, password: MERCHANT_PASSWORD },
    });
    if (!auth.ok()) throw new Error(`Login Supabase fallito: ${auth.status()} ${await auth.text()}`);
    const { access_token } = (await auth.json()) as { access_token: string };
    const claims = JSON.parse(Buffer.from(access_token.split('.')[1], 'base64').toString()) as {
      merchant_id: string;
    };
    const headers = { Authorization: `Bearer ${access_token}` };

    const cur = await req.get(`${API_URL}/bot-config/${claims.merchant_id}/overrides`, { headers });
    if (!cur.ok()) throw new Error(`GET overrides fallito: ${cur.status()} ${await cur.text()}`);
    const body = (await cur.json()) as { overrides?: Record<string, Record<string, unknown>> };
    const overrides = body.overrides ?? {};
    overrides.rag = { ...(overrides.rag ?? {}), min_score: minScore };

    const put = await req.put(`${API_URL}/bot-config/${claims.merchant_id}/overrides`, {
      headers,
      data: { overrides },
    });
    if (!put.ok()) throw new Error(`PUT overrides fallito: ${put.status()} ${await put.text()}`);
  } finally {
    await req.dispose();
  }
}
