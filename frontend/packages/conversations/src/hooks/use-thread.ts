'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useConversationsContext } from '../lib/context';
import type { Conversation, Message } from '../types';
import { CONV_LIST_KEY } from './use-conversations';

const REALTIME_FALLBACK_MS = 30_000;
const PREVIEW_MAX_CHARS = 80;

export const threadQueryKey = (conversationId: string) =>
  ['conversations', 'thread', conversationId] as const;

/** Splice the last message of a thread into every cached conversations list,
 *  so the rail row's preview/role/last_message_at no longer falls back to
 *  "N messaggi". Called whenever thread data resolves or realtime fires. */
function hydrateListPreview(
  queryClient: ReturnType<typeof useQueryClient>,
  conversationId: string,
  messages: Message[],
) {
  if (messages.length === 0) return;
  const last = messages[messages.length - 1];
  if (!last) return;
  const preview = last.content.length > PREVIEW_MAX_CHARS
    ? `${last.content.slice(0, PREVIEW_MAX_CHARS - 1).trimEnd()}…`
    : last.content;

  queryClient.setQueriesData<Conversation[]>(
    { queryKey: CONV_LIST_KEY },
    (prev) => {
      if (!prev) return prev;
      let touched = false;
      const next = prev.map((c) => {
        if (c.id !== conversationId) return c;
        if (
          c.last_message_preview === preview &&
          c.last_message_role === last.role &&
          c.last_message_at === last.created_at
        ) {
          return c;
        }
        touched = true;
        return {
          ...c,
          last_message_preview: preview,
          last_message_role: last.role,
          last_message_at: last.created_at,
        };
      });
      return touched ? next : prev;
    },
  );
}

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
          'id, conversation_id, role, direction, content, status, client_message_id, wa_message_id, delivered_at, read_at, failed_at, error, meta, created_at',
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

  // Whenever thread data updates (initial fetch, realtime invalidation,
  // optimistic insert), splice the latest into the conversations list cache.
  useEffect(() => {
    if (!conversationId || !query.data) return;
    hydrateListPreview(queryClient, conversationId, query.data);
  }, [queryClient, conversationId, query.data]);

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
