/**
 * Wrapper around @supabase/supabase-js. Everything that touches Supabase goes
 * through here so the dependency stays swappable (section 15 — "Lock-in
 * Supabase Auth"). Do not import @supabase/supabase-js directly from apps.
 */
import { createBrowserClient, createServerClient, type CookieOptions } from '@supabase/ssr';
import type { Database } from './types';

export type { Database } from './types';
export type { Session, User } from '@supabase/supabase-js';

export type SupabaseBrowserConfig = {
  url: string;
  anonKey: string;
  /**
   * Optional Bearer to send on every PostgREST/Storage request, instead of the
   * supabase-js session. Used during agency→merchant impersonation, where there
   * is no supabase-js session and the merchant-scoped token lives in a cookie
   * (symmetric to how `web-merchant/lib/api.ts` and `realtime-auth-gate.tsx`
   * already use it). The token is HS256-signed with the project's
   * SUPABASE_JWT_SECRET. Realtime is authorized separately via
   * `realtime.setAuth` in the RealtimeAuthGate — which only works because the
   * client below stays a singleton (see the call to createBrowserClient).
   */
  accessToken?: string;
};

/**
 * Options passed to `@supabase/ssr`'s `createBrowserClient`. Extracted as a pure
 * function so the impersonation/singleton invariant is unit-testable without a
 * real Supabase client.
 *
 * Invariant: NEVER set `isSingleton: false`. @supabase/ssr caches one instance
 * per browser context; RealtimeAuthGate calls
 * `getBrowserSupabase().realtime.setAuth(token)` on that shared instance, and
 * every Realtime consumer (`conversations-route`, `agenda/use-appointments`,
 * `dashboard/merchant-dashboard`) creates its `.channel()` subscriptions on the
 * same instance — so the impersonation auth reaches them all. A fresh client per
 * call would split setAuth from the channels, leaving Realtime as anon (RLS
 * blocks → 30s poll fallback).
 *
 * When impersonating we only override the REST/Storage session Bearer with the
 * merchant token via a static Authorization header (there is no supabase-js
 * session to drive it); Realtime carries the same token via `setAuth`.
 */
export function browserClientOptions(accessToken?: string) {
  return accessToken
    ? { global: { headers: { Authorization: `Bearer ${accessToken}` } } }
    : {};
}

export function createBrowserSupabase({ url, anonKey, accessToken }: SupabaseBrowserConfig) {
  return createBrowserClient<Database>(url, anonKey, browserClientOptions(accessToken));
}

export type ServerCookieAdapter = {
  getAll: () => { name: string; value: string }[];
  setAll: (cookies: { name: string; value: string; options: CookieOptions }[]) => void;
};

export function createServerSupabase(
  { url, anonKey }: SupabaseBrowserConfig,
  cookies: ServerCookieAdapter,
) {
  return createServerClient<Database>(url, anonKey, {
    cookies,
  });
}
