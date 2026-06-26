'use client';

import { useQuery } from '@tanstack/react-query';
import { getBrowserSupabase } from '@/lib/supabase';
import { IMP_COOKIE, impTokenValid, decodeJwtPayload, readCookieBrowser } from '@/lib/impersonation';

export interface GhlSyncEntry {
  id: string;
  lead_id: string | null;
  conversation_id: string | null;
  operation: string;
  ghl_entity_type: string | null;
  ghl_entity_id: string | null;
  status: string;
  error_detail: string | null;
  payload: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  occurred_at: string;
}

async function getAuthToken(): Promise<string | null> {
  const imp = readCookieBrowser(IMP_COOKIE);
  if (imp && impTokenValid(decodeJwtPayload(imp))) return imp;
  const supabase = getBrowserSupabase();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export function useGhlSyncLog(options?: { sinceDays?: number; limit?: number }) {
  const sinceDays = options?.sinceDays ?? 30;
  const limit = options?.limit ?? 100;

  return useQuery({
    queryKey: ['integrations', 'ghl-sync-log', sinceDays, limit],
    refetchInterval: 60_000,
    queryFn: async (): Promise<GhlSyncEntry[]> => {
      const token = await getAuthToken();
      const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';
      const url = `${base}/integrations/ghl/sync-log?since_days=${sinceDays}&limit=${limit}`;
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error(`sync-log ${res.status}`);
      const json = (await res.json()) as { entries: GhlSyncEntry[] };
      return json.entries;
    },
  });
}
