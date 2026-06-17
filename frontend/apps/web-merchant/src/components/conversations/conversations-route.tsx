'use client';

import { ConversationsProvider, ConversationsWorkspace } from '@reloop/conversations';
import { useQuery } from '@tanstack/react-query';
import type { Route } from 'next';
import { useRouter } from 'next/navigation';
import { useMemo } from 'react';
import { getApiClient } from '@/lib/api';
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
      composerEnabled
      merchantAutoReplyEnabled={merchantAutoReplyEnabled}
      customerDetailEnabled
    >
      <div className="h-full overflow-hidden">
        <ConversationsWorkspace selectedId={selectedId} onSelect={handleSelect} />
      </div>
    </ConversationsProvider>
  );
}
