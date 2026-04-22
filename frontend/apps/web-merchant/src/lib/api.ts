import { createReloopClient } from '@reloop/api-client';
import { getBrowserSupabase } from './supabase';

export const getApiClient = () => {
  const supabase = getBrowserSupabase();
  return createReloopClient({
    baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL!,
    getAccessToken: async () => {
      const { data } = await supabase.auth.getSession();
      return data.session?.access_token ?? null;
    },
  });
};
