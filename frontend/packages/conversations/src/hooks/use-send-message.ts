'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import { threadQueryKey } from './use-thread';
import type { Message } from '../types';

interface SendArgs {
  conversationId: string;
  text: string;
  /** Caller-provided UUID for optimistic reconciliation + idempotent retry. */
  clientMessageId: string;
}

/**
 * Optimistic send: insert a synthetic `pending` message into the thread cache
 * keyed by `client_message_id`, then POST to FastAPI. The canonical row that
 * comes back (or the Realtime INSERT, whichever is first) replaces the
 * synthetic one. On failure, we mark the synthetic row as `failed` so the
 * composer can surface a retry affordance.
 */
export function useSendMessage() {
  const { supabase, apiBaseUrl } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ conversationId, text, clientMessageId }: SendArgs) => {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) throw new Error('Sessione scaduta. Effettua il login.');

      const res = await fetch(`${apiBaseUrl}/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ text, client_message_id: clientMessageId }),
      });

      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      return (await res.json()) as Message;
    },

    onMutate: async ({ conversationId, text, clientMessageId }) => {
      const key = threadQueryKey(conversationId);
      await queryClient.cancelQueries({ queryKey: key });

      const previous = queryClient.getQueryData<Message[]>(key);
      const optimistic: Message = {
        id: `optimistic:${clientMessageId}`,
        conversation_id: conversationId,
        role: 'agent',
        direction: 'out',
        content: text,
        status: 'pending',
        client_message_id: clientMessageId,
        wa_message_id: null,
        delivered_at: null,
        read_at: null,
        failed_at: null,
        error: null,
        created_at: new Date().toISOString(),
      };

      queryClient.setQueryData<Message[]>(key, (old) => [...(old ?? []), optimistic]);
      return { previous, conversationId, clientMessageId };
    },

    onError: (err, _vars, ctx) => {
      if (!ctx) return;
      const key = threadQueryKey(ctx.conversationId);
      // Mark the optimistic row as failed instead of rolling back; the user
      // can hit "retry" which re-issues the same client_message_id.
      queryClient.setQueryData<Message[]>(key, (old) => {
        if (!old) return old;
        return old.map((m) =>
          m.client_message_id === ctx.clientMessageId
            ? {
                ...m,
                status: 'failed' as const,
                failed_at: new Date().toISOString(),
                error: { message: err instanceof Error ? err.message : String(err) },
              }
            : m,
        );
      });
    },

    onSuccess: (saved, _vars, ctx) => {
      if (!ctx) return;
      const key = threadQueryKey(ctx.conversationId);
      // Replace the optimistic row with the canonical one (matched by client_message_id).
      queryClient.setQueryData<Message[]>(key, (old) => {
        if (!old) return [saved];
        const idx = old.findIndex((m) => m.client_message_id === ctx.clientMessageId);
        if (idx === -1) return [...old, saved];
        const next = old.slice();
        next[idx] = saved;
        return next;
      });
    },
  });
}
