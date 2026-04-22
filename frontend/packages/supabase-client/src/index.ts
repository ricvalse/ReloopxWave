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
};

export function createBrowserSupabase({ url, anonKey }: SupabaseBrowserConfig) {
  return createBrowserClient<Database>(url, anonKey);
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
