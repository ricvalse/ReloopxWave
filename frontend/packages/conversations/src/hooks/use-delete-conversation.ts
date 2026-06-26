'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import { CONV_LIST_KEY } from './use-conversations';
import type { Conversation } from '../types';

export function useDeleteConversation() {
  const { supabase, apiBaseUrl, getAccessToken } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (conversationId: string) => {
      const token = getAccessToken
        ? await getAccessToken()
        : (await supabase.auth.getSession()).data.session?.access_token ?? null;
      if (!token) throw new Error('Sessione scaduta. Effettua il login.');

      const res = await fetch(`${apiBaseUrl}/conversations/${conversationId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
      }
    },

    onMutate: async (conversationId) => {
      await queryClient.cancelQueries({ queryKey: CONV_LIST_KEY });

      const keys = queryClient.getQueriesData<Conversation[]>({ queryKey: CONV_LIST_KEY });
      const snapshot = keys.map(([key, value]) => [key, value] as const);

      for (const [key, value] of keys) {
        if (!value) continue;
        queryClient.setQueryData<Conversation[]>(
          key,
          value.filter((c) => c.id !== conversationId),
        );
      }
      return { snapshot };
    },

    onError: (_err, _id, ctx) => {
      if (!ctx) return;
      for (const [key, value] of ctx.snapshot) {
        queryClient.setQueryData(key, value);
      }
    },

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: CONV_LIST_KEY });
    },
  });
}
