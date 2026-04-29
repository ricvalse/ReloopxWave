'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useConversationsContext } from '../lib/context';
import type { Conversation } from '../types';

const CONV_LIST_KEY = ['conversations', 'list'] as const;
const REALTIME_FALLBACK_MS = 30_000;

export function useConversations({ limit = 100 }: { limit?: number } = {}) {
  const { supabase, merchantFilter } = useConversationsContext();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: [...CONV_LIST_KEY, { merchantFilter, limit }],
    queryFn: async (): Promise<Conversation[]> => {
      let req = supabase
        .from('conversations')
        .select(
          'id, merchant_id, wa_contact_phone, wa_phone_number_id, status, last_message_at, message_count, auto_reply, meta, created_at',
        )
        .order('last_message_at', { ascending: false, nullsFirst: false })
        .limit(limit);
      if (merchantFilter) {
        req = req.eq('merchant_id', merchantFilter);
      }
      const { data, error } = await req;
      if (error) throw error;
      return (data ?? []) as unknown as Conversation[];
    },
    // Realtime is canonical; circuit-breaker poll only when no event for 30s.
    refetchInterval: REALTIME_FALLBACK_MS,
    refetchIntervalInBackground: false,
  });

  // Realtime: invalidate the list on any new message insert (tenant-scoped via RLS).
  useEffect(() => {
    const channel = supabase
      .channel('conversations:list')
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'messages' },
        () => {
          queryClient.invalidateQueries({ queryKey: CONV_LIST_KEY });
        },
      )
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'conversations' },
        () => {
          queryClient.invalidateQueries({ queryKey: CONV_LIST_KEY });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [supabase, queryClient]);

  return query;
}
