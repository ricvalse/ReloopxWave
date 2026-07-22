import { createReloopClient } from '@reloop/api-client';
import { getBrowserSupabase } from './supabase';
import { IMP_COOKIE, activeImpersonationToken, readCookieBrowser } from './impersonation';

export const getApiClient = () => {
  const supabase = getBrowserSupabase();
  return createReloopClient({
    baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL!,
    getAccessToken: async () => {
      // While impersonating, the backend Bearer is the merchant-scoped
      // impersonation token (there is no supabase-js session). Prefer it when a
      // valid cookie is present; otherwise fall back to the real session.
      const imp = activeImpersonationToken(readCookieBrowser(IMP_COOKIE));
      if (imp) return imp;
      const { data } = await supabase.auth.getSession();
      return data.session?.access_token ?? null;
    },
    onSessionInvalid: redirectOnInvalidSession,
  });
};

/**
 * `imp-access-token` is a single cookie shared by every tab on this origin —
 * it isn't scoped per merchant or per tab. Impersonating a second merchant in
 * another tab (or clicking "Esci" there) silently overwrites/clears it, so a
 * tab left open on the first merchant keeps sending its (now stale) merchant_id
 * in the URL against a Bearer that belongs to someone else or doesn't exist.
 * The backend correctly 403s every call in that tab at once — this turns that
 * into one clear redirect instead of every panel showing its own generic
 * "errore nel caricamento" message.
 */
const SESSION_ERROR_CODES = new Set([
  'missing_token',
  'invalid_token',
  'missing_tenant_claim',
  'missing_sub_claim',
  'unknown_kid',
  'jwt_not_configured',
  'cross_merchant_access',
]);
const SWITCHED_ERROR_CODES = new Set(['cross_merchant_access', 'missing_token']);

let redirectingOnInvalidSession = false;

async function redirectOnInvalidSession(response: Response): Promise<void> {
  if (typeof window === 'undefined' || redirectingOnInvalidSession) return;
  if (response.status !== 403) return;
  const path = window.location.pathname;
  if (path === '/impersonate' || path.startsWith('/impersonation-expired')) return;

  let code: string | undefined;
  try {
    const body = (await response.clone().json()) as { error?: { code?: string } };
    code = body?.error?.code;
  } catch {
    return;
  }
  if (!code || !SESSION_ERROR_CODES.has(code)) return;

  redirectingOnInvalidSession = true;
  const isImpersonating = !!readCookieBrowser(IMP_COOKIE);
  if (isImpersonating) {
    const reason = SWITCHED_ERROR_CODES.has(code) ? 'switched' : 'expired';
    window.location.href = `/impersonation-expired?reason=${reason}`;
  } else {
    window.location.href = '/login';
  }
}

/**
 * Extract a human-readable message from an API error body. The backend's domain
 * errors serialize as `{ error: { code, message } }`; FastAPI validation errors
 * as `{ detail: ... }`. Falls back gracefully instead of dumping raw JSON.
 */
export function apiErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object') {
    const rec = error as Record<string, unknown>;
    const nested = rec.error;
    if (nested && typeof nested === 'object' && 'message' in nested) {
      const m = (nested as Record<string, unknown>).message;
      if (typeof m === 'string') return m;
    }
    if (typeof rec.detail === 'string') return rec.detail;
    if (rec.detail && typeof rec.detail === 'object') {
      const errs = (rec.detail as Record<string, unknown>).errors;
      if (Array.isArray(errs)) {
        const msgs = errs
          .map((e) => (e && typeof e === 'object' ? (e as Record<string, unknown>).message : null))
          .filter((m): m is string => typeof m === 'string');
        if (msgs.length) return msgs.join(', ');
      }
    }
  }
  return 'Errore imprevisto';
}
