'use client';

import { ConversationsProvider, ConversationsWorkspace } from '@reloop/conversations';
import type { Route } from 'next';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useMemo } from 'react';
import { getBrowserSupabase } from '@/lib/supabase';
import { MerchantPicker } from './merchant-picker';

interface InboxRouteProps {
  selectedId: string | null;
}

export function InboxRoute({ selectedId }: InboxRouteProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const supabase = useMemo(() => getBrowserSupabase(), []);
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL!;

  const merchantFilter = searchParams.get('merchant');

  const buildHref = useCallback(
    (path: string) => {
      const qs = merchantFilter ? `?merchant=${encodeURIComponent(merchantFilter)}` : '';
      return `${path}${qs}` as Route;
    },
    [merchantFilter],
  );

  const handleSelect = (id: string | null) => {
    router.push(buildHref(id ? `/inbox/${id}` : '/inbox'));
  };

  const handleMerchantChange = (merchantId: string | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (merchantId) {
      params.set('merchant', merchantId);
    } else {
      params.delete('merchant');
    }
    const base = selectedId ? `/inbox/${selectedId}` : '/inbox';
    const qs = params.toString();
    router.push((qs ? `${base}?${qs}` : base) as Route);
  };

  return (
    <ConversationsProvider
      supabase={supabase}
      apiBaseUrl={apiBaseUrl}
      composerEnabled
      adminMode
      customerDetailEnabled
      merchantFilter={merchantFilter}
    >
      <div className="flex h-full min-h-0 flex-col overflow-hidden">
        <div className="flex shrink-0 items-center gap-2 border-b border-border bg-card px-4 py-2">
          <MerchantPicker value={merchantFilter} onChange={handleMerchantChange} />
        </div>
        <div className="min-h-0 flex-1">
          <ConversationsWorkspace selectedId={selectedId} onSelect={handleSelect} />
        </div>
      </div>
    </ConversationsProvider>
  );
}
