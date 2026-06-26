'use client';

import { ConversationsProvider, ConversationsWorkspace } from '@reloop/conversations';
import { useQuery } from '@tanstack/react-query';
import type { Route } from 'next';
import { useRouter } from 'next/navigation';
import { useCallback, useMemo } from 'react';
import { getApiClient } from '@/lib/api';
import { IMP_COOKIE, decodeJwtPayload, impTokenValid, readCookieBrowser } from '@/lib/impersonation';
import { getBrowserSupabase } from '@/lib/supabase';
import { useMerchantId } from '@/hooks/use-merchant-id';

interface ConversationsRouteProps {
  selectedId: string | null;
}

export function ConversationsRoute({ selectedId }: ConversationsRouteProps) {
  const router = useRouter();
  const supabase = useMemo(() => getBrowserSupabase(), []);
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL!;
  const { merchantId } = useMerchantId();

  // During agency→merchant impersonation there is no supabase-js session on
  // web-merchant (the HS256 token lives in a cookie, not in Supabase auth storage).
  // Symmetric to lib/api.ts: prefer the impersonation cookie, fall back to the
  // real Supabase session for normal merchant-user logins.
  const getAccessToken = useCallback(async (): Promise<string | null> => {
    const imp = readCookieBrowser(IMP_COOKIE);
    if (imp && impTokenValid(decodeJwtPayload(imp))) return imp;
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  }, [supabase]);

  // Read the merchant-level master switch via the resolved bot-config endpoint
  // (cascade: merchant override → agency template → system default). Shares the
  // single ['bot-config','resolved',merchantId] cache entry with the bot-config
  // panel + setup checklist — one fetch per merchant, narrowed here via `select`.
  const botConfig = useQuery({
    queryKey: ['bot-config', 'resolved', merchantId],
    enabled: !!merchantId,
    staleTime: 60_000,
    queryFn: async (): Promise<Record<string, unknown>> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/resolved', {
        params: { path: { merchant_id: merchantId! } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as Record<string, unknown>) ?? {};
    },
    // fail-closed: undefined data (error/loading) → auto-reply disabled
    select: (d) => (d as { bot?: { auto_reply_enabled?: boolean } }).bot?.auto_reply_enabled ?? false,
  });
  const merchantAutoReplyEnabled = botConfig.data ?? false;

  const handleSelect = (id: string | null) => {
    if (id) {
      router.push(`/conversations/${id}` as Route);
    } else {
      router.push('/conversations' as Route);
    }
  };

  return (
    <ConversationsProvider
      supabase={supabase}
      apiBaseUrl={apiBaseUrl}
      getAccessToken={getAccessToken}
      composerEnabled
      merchantAutoReplyEnabled={merchantAutoReplyEnabled}
      customerDetailEnabled
      dsarEnabled
    >
      <div className="absolute inset-0 overflow-hidden">
        <ConversationsWorkspace selectedId={selectedId} onSelect={handleSelect} />
      </div>
    </ConversationsProvider>
  );
}
