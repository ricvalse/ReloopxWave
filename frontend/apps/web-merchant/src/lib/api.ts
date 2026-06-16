import { createReloopClient } from '@reloop/api-client';
import { getBrowserSupabase } from './supabase';
import { IMP_COOKIE, decodeJwtPayload, impTokenValid, readCookieBrowser } from './impersonation';

export const getApiClient = () => {
  const supabase = getBrowserSupabase();
  return createReloopClient({
    baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL!,
    getAccessToken: async () => {
      // While impersonating, the backend Bearer is the merchant-scoped
      // impersonation token (there is no supabase-js session). Prefer it when a
      // valid cookie is present; otherwise fall back to the real session.
      const imp = readCookieBrowser(IMP_COOKIE);
      if (imp && impTokenValid(decodeJwtPayload(imp))) {
        return imp;
      }
      const { data } = await supabase.auth.getSession();
      return data.session?.access_token ?? null;
    },
  });
};

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
