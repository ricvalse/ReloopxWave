'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useConversationsContext } from '../lib/context';
import type { Message } from '../types';

const REALTIME_FALLBACK_MS = 30_000;

export const threadQueryKey = (conversationId: string) =>
  ['conversations', 'thread', conversationId] as const;

export function useThread(conversationId: string | null) {
  const { supabase } = useConversationsContext();
  const queryClient = useQueryClient();

  const query = useQuery({
    enabled: !!conversationId,
    queryKey: threadQueryKey(conversationId ?? ''),
    queryFn: async (): Promise<Message[]> => {
      const { data, error } = await supabase
        .from('messages')
        .select(
          'id, conversation_id, role, direction, content, status, client_message_id, wa_message_id, delivered_at, read_at, failed_at, error, created_at',
        )
        .eq('conversation_id', conversationId!)
        .order('created_at', { ascending: true })
        .limit(500);
      if (error) throw error;
      return (data ?? []) as unknown as Message[];
    },
    refetchInterval: REALTIME_FALLBACK_MS,
    refetchIntervalInBackground: false,
  });

  // Realtime: subscribe to inserts AND updates on this thread's messages so
  // the tick state machine (pending → sent → delivered → read) lights up live.
  useEffect(() => {
    if (!conversationId) return;
    const channel = supabase
      .channel(`conversations:thread:${conversationId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'messages',
          filter: `conversation_id=eq.${conversationId}`,
        },
        () => {
          queryClient.invalidateQueries({ queryKey: threadQueryKey(conversationId) });
        },
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'messages',
          filter: `conversation_id=eq.${conversationId}`,
        },
        () => {
          queryClient.invalidateQueries({ queryKey: threadQueryKey(conversationId) });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [supabase, queryClient, conversationId]);

  return query;
}
