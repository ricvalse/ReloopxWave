'use client';

import { useEffect } from 'react';
import { getBrowserSupabase } from '@/lib/supabase';
import {
  IMP_COOKIE,
  decodeJwtPayload,
  impTokenValid,
  readCookieBrowser,
} from '@/lib/impersonation';

const REASSERT_MS = 10_000;

/**
 * Authorizes the Supabase Realtime websocket as the impersonated merchant.
 *
 * During agency→merchant impersonation there is no supabase-js session (by
 * design — the impersonation token carries no refresh token), so Realtime would
 * otherwise authenticate with the agency admin's session token and RLS would
 * silently block every merchant channel — leaving the 30s polling fallback as
 * the only data path ("i dati ci mettono molto ad arrivare"). The impersonation
 * token is HS256-signed with the project's SUPABASE_JWT_SECRET, so Supabase
 * Realtime accepts it: we just hand it to `realtime.setAuth`.
 *
 * No-op when not impersonating (supabase-js drives Realtime auth from the real
 * session). At token expiry it reverts to the anon/session token and stops —
 * web-merchant cannot re-mint, so the expiry UX (banner + middleware) takes over.
 * Renders nothing.
 *
 * Invariant: `getBrowserSupabase()` returns the browser singleton, so this one
 * mount point covers every Realtime consumer in the app.
 */
export function RealtimeAuthGate() {
  useEffect(() => {
    const token = readCookieBrowser(IMP_COOKIE);
    const claims = token ? decodeJwtPayload(token) : null;
    if (!token || !impTokenValid(claims)) return; // not impersonating → no-op

    const realtime = getBrowserSupabase().realtime;
    void realtime.setAuth(token);

    const expMs = (claims.exp ?? 0) * 1000;
    const interval = setInterval(() => {
      if (Date.now() >= expMs) {
        // Merchant token expired — drop it from the socket; don't keep a dead
        // token authorized. No re-mint possible from here.
        void realtime.setAuth();
        clearInterval(interval);
        return;
      }
      // Re-assert: heals a stray supabase-js SIGNED_OUT that would reset the
      // socket to anon. Cheap — realtime-js skips the push when unchanged.
      void realtime.setAuth(token);
    }, REASSERT_MS);

    return () => clearInterval(interval);
  }, []);

  return null;
}
