import { createBrowserSupabase, createServerSupabase } from '@reloop/supabase-client';
import { parsePublicEnv } from '@reloop/config/env';
import { IMP_COOKIE, decodeJwtPayload, impTokenValid, readCookieBrowser } from './impersonation';

const publicEnv = () =>
  parsePublicEnv({
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  });

/**
 * When the browser holds a valid impersonation token, the direct-to-Supabase
 * reads (conversations, agenda, settings, KB) must carry it as the Bearer —
 * otherwise PostgREST authenticates as the agency admin (or anon) and RLS
 * silently blocks every merchant row. Symmetric to `lib/api.ts` (backend
 * Bearer) and `realtime-auth-gate.tsx` (websocket auth). Returns undefined on
 * the server and when not impersonating, so the client falls back to the
 * supabase-js session as before.
 *
 * `createBrowserSupabase` keeps the client a singleton, so the Realtime path is
 * authorized once by RealtimeAuthGate (`realtime.setAuth`) on the same shared
 * instance these reads use — REST and Realtime stay in sync under impersonation.
 */
const impAccessTokenIfValid = (): string | undefined => {
  const token = readCookieBrowser(IMP_COOKIE);
  if (token && impTokenValid(decodeJwtPayload(token))) return token;
  return undefined;
};

export const getBrowserSupabase = () => {
  const env = publicEnv();
  return createBrowserSupabase({
    url: env.NEXT_PUBLIC_SUPABASE_URL,
    anonKey: env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    accessToken: impAccessTokenIfValid(),
  });
};

export const getServerSupabase = (cookieAdapter: Parameters<typeof createServerSupabase>[1]) => {
  const env = publicEnv();
  return createServerSupabase(
    { url: env.NEXT_PUBLIC_SUPABASE_URL, anonKey: env.NEXT_PUBLIC_SUPABASE_ANON_KEY },
    cookieAdapter,
  );
};
