import { createBrowserSupabase, createServerSupabase } from '@reloop/supabase-client';
import { parsePublicEnv } from '@reloop/config/env';

const publicEnv = () =>
  parsePublicEnv({
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  });

export const getBrowserSupabase = () => {
  const env = publicEnv();
  return createBrowserSupabase({
    url: env.NEXT_PUBLIC_SUPABASE_URL,
    anonKey: env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  });
};

export const getServerSupabase = (cookieAdapter: Parameters<typeof createServerSupabase>[1]) => {
  const env = publicEnv();
  return createServerSupabase(
    { url: env.NEXT_PUBLIC_SUPABASE_URL, anonKey: env.NEXT_PUBLIC_SUPABASE_ANON_KEY },
    cookieAdapter,
  );
};
