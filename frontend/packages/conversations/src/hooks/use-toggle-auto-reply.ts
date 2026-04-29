'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import type { Conversation } from '../types';

interface ToggleArgs {
  conversationId: string;
  autoReply: boolean;
}

/**
 * Flip the per-thread bot takeover switch. Direct Supabase update under RLS —
 * the merchant_user role can update its own `conversations.auto_reply`.
 *
 * Optimistic: the row is patched in the conversations list cache before the
 * round-trip, then reconciled by the Realtime UPDATE event. On error we roll
 * back so the switch snaps to the server's truth.
 */
export function useToggleAutoReply() {
  const { supabase } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ conversationId, autoReply }: ToggleArgs) => {
      const { data, error } = await supabase
        .from('conversations')
        .update({ auto_reply: autoReply })
        .eq('id', conversationId)
        .select('id, auto_reply')
        .single();
      if (error) throw error;
      return data as { id: string; auto_reply: boolean };
    },

    onMutate: async ({ conversationId, autoReply }) => {
      const keys = queryClient.getQueriesData<Conversation[]>({
        queryKey: ['conversations', 'list'],
      });
      const snapshot = keys.map(([key, value]) => [key, value] as const);

      for (const [key, value] of keys) {
        if (!value) continue;
        queryClient.setQueryData<Conversation[]>(
          key,
          value.map((c) =>
            c.id === conversationId ? { ...c, auto_reply: autoReply } : c,
          ),
        );
      }
      return { snapshot };
    },

    onError: (_err, _vars, ctx) => {
      if (!ctx) return;
      for (const [key, value] of ctx.snapshot) {
        queryClient.setQueryData(key, value);
      }
    },
  });
}
