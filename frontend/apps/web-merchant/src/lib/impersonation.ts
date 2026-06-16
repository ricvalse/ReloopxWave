/**
 * Agency→merchant impersonation helpers (frontend side).
 *
 * The backend mints a short-lived, merchant-scoped Supabase access token
 * (see backend `integrations/impersonation.py`). web-merchant accepts it as an
 * *alternative* session carried in the non-httpOnly `imp-access-token` cookie —
 * deliberately NOT via `supabase.auth.setSession`, because the token has no
 * valid refresh token and we don't want supabase-js looping on failed refreshes.
 *
 * These helpers are framework-agnostic (no `next/*` imports) so they run in the
 * edge middleware, server components, and the browser alike. The token is only
 * *read* here for display + session-shaping; every API call is re-verified by
 * the backend.
 */

export const IMP_COOKIE = 'imp-access-token';
export const IMP_META_COOKIE = 'imp-meta';

export type ImpClaims = {
  sub?: string;
  exp?: number;
  tenant_id?: string;
  merchant_id?: string;
  user_role?: string;
  app_metadata?: Record<string, unknown>;
  user_metadata?: Record<string, unknown>;
  act?: { sub?: string; type?: string; email?: string };
  session_id?: string;
};

export type ImpMeta = {
  merchantName: string;
  expiresAt: number; // epoch seconds
  sessionId: string;
};

/** A minimal session shape both the real Supabase session and an impersonation
 * session normalize to — the only fields the merchant layout reads. */
export type MerchantSession = {
  user: {
    email?: string | null;
    app_metadata?: Record<string, unknown>;
    user_metadata?: Record<string, unknown>;
  };
  isImpersonation: boolean;
};

function base64UrlToJson(segment: string): unknown {
  const b64 = segment.replace(/-/g, '+').replace(/_/g, '/');
  const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
  let binary: string;
  if (typeof atob === 'function') {
    binary = atob(padded);
  } else {
    // Node server runtime without atob (older runtimes).
    binary = Buffer.from(padded, 'base64').toString('binary');
  }
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  const text = new TextDecoder().decode(bytes);
  return JSON.parse(text);
}

/** Decode a JWT payload WITHOUT verifying its signature. Display/shaping only. */
export function decodeJwtPayload(token: string): ImpClaims | null {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    return base64UrlToJson(payload) as ImpClaims;
  } catch {
    return null;
  }
}

/** True when the token is an impersonation token that hasn't expired yet. */
export function impTokenValid(claims: ImpClaims | null): claims is ImpClaims {
  if (!claims || typeof claims.exp !== 'number') return false;
  if (claims.act?.type !== 'impersonation') return false;
  return claims.exp * 1000 > Date.now();
}

/** Shape an impersonation token's claims into the common MerchantSession. */
export function impSessionFromClaims(claims: ImpClaims): MerchantSession {
  return {
    user: {
      email: claims.act?.email ?? null,
      app_metadata: claims.app_metadata ?? {
        tenant_id: claims.tenant_id,
        merchant_id: claims.merchant_id,
        role: claims.user_role,
      },
      user_metadata: claims.user_metadata ?? {},
    },
    isImpersonation: true,
  };
}

/** Clear both impersonation cookies — ends the impersonation session. No-op on
 * the server. The caller decides where to navigate afterwards. */
export function clearImpCookiesBrowser(): void {
  if (typeof document === 'undefined') return;
  const expire = 'path=/; max-age=0; samesite=lax';
  document.cookie = `${IMP_COOKIE}=; ${expire}`;
  document.cookie = `${IMP_META_COOKIE}=; ${expire}`;
}

/** Read a cookie value in the browser. Returns null on the server. */
export function readCookieBrowser(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const prefix = `${name}=`;
  const found = document.cookie.split('; ').find((c) => c.startsWith(prefix));
  return found ? decodeURIComponent(found.slice(prefix.length)) : null;
}

/** True when the browser currently holds a valid impersonation token. */
export function isImpersonatingBrowser(): boolean {
  const token = readCookieBrowser(IMP_COOKIE);
  return !!token && impTokenValid(decodeJwtPayload(token));
}
