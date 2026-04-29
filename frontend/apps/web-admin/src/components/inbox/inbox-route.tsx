'use client';

import { ConversationsProvider, ConversationsWorkspace } from '@reloop/conversations';
import type { Route } from 'next';
import { useRouter } from 'next/navigation';
import { useMemo } from 'react';
import { getBrowserSupabase } from '@/lib/supabase';

interface InboxRouteProps {
  selectedId: string | null;
}

export function InboxRoute({ selectedId }: InboxRouteProps) {
  const router = useRouter();
  const supabase = useMemo(() => getBrowserSupabase(), []);
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL!;

  const handleSelect = (id: string | null) => {
    if (id) {
      router.push(`/inbox/${id}` as Route);
    } else {
      router.push('/inbox' as Route);
    }
  };

  return (
    <ConversationsProvider
      supabase={supabase}
      apiBaseUrl={apiBaseUrl}
      composerEnabled
      adminMode
    >
      <div className="h-full">
        <ConversationsWorkspace selectedId={selectedId} onSelect={handleSelect} />
      </div>
    </ConversationsProvider>
  );
}
