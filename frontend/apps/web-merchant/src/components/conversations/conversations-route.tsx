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
  // (cascade: merchant override → agency template → system default).
  const botConfig = useQuery({
    queryKey: ['bot-config', 'resolved', merchantId, 'auto_reply_only'],
    enabled: !!merchantId,
    staleTime: 60_000,
    queryFn: async (): Promise<boolean> => {
      const api = getApiClient();
      const { data, error } = await api.GET(
        '/bot-config/{merchant_id}/resolved' as never,
        { params: { path: { merchant_id: merchantId } } } as never,
      );
      if (error) return true; // fail-open: assume enabled if we can't read
      const resolved = data as { bot?: { auto_reply_enabled?: boolean } };
      return resolved.bot?.auto_reply_enabled ?? true;
    },
  });
  const merchantAutoReplyEnabled = botConfig.data ?? true;

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
    >
      <div className="h-full">
        <ConversationsWorkspace selectedId={selectedId} onSelect={handleSelect} />
      </div>
    </ConversationsProvider>
  );
}
