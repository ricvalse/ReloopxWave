'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import { CONV_LIST_KEY } from './use-conversations';
import { authedConversationPost } from './use-ai-pause';
import type { Conversation } from '../types';

interface ToggleArgs {
  conversationId: string;
  autoReply: boolean;
}

/**
 * Flip the per-thread bot takeover switch.
 *
 * Goes through the API, not straight to Supabase. `auto_reply` is only one of
 * the fields that make up a handoff episode: `handoff_at`, `handoff_resolved_at`
 * and the ESCALATED FSM state have to move with it. Writing the column on its
 * own left threads in a state nothing downstream could read — bot answering but
 * the episode still open, so proactive automations stayed muted on that thread
 * and the SLA sweep kept raising alerts nobody could clear.
 *
 * Optimistic: the row is patched in the conversations list cache before the
 * round-trip, then reconciled with the server's row. On error we roll back so
 * the switch snaps to the server's truth.
 */
export function useToggleAutoReply() {
  const { supabase, apiBaseUrl, getAccessToken } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ conversationId, autoReply }: ToggleArgs) =>
      authedConversationPost(
        supabase,
        apiBaseUrl,
        `/conversations/${conversationId}/${autoReply ? 'ai-resume' : 'ai-takeover'}`,
        undefined,
        getAccessToken,
      ),

    onMutate: async ({ conversationId, autoReply }) => {
      const snapshot = queryClient.getQueriesData<Conversation[]>({
        queryKey: CONV_LIST_KEY,
      });
      const now = new Date().toISOString();
      queryClient.setQueriesData<Conversation[]>({ queryKey: CONV_LIST_KEY }, (old) =>
        old?.map((c) =>
          c.id === conversationId
            ? {
                ...c,
                auto_reply: autoReply,
                // Mirror the server: resuming closes the episode, taking over
                // opens one. Without this the banner and the "Da gestire" tab
                // disagree with the switch until the next refetch.
                handoff_at: autoReply ? c.handoff_at : now,
                handoff_resolved_at: autoReply ? now : null,
                ai_disabled_until: autoReply ? null : c.ai_disabled_until,
              }
            : c,
        ),
      );
      return { snapshot };
    },

    onSuccess: (conv, { conversationId }) => {
      queryClient.setQueriesData<Conversation[]>({ queryKey: CONV_LIST_KEY }, (old) =>
        old?.map((c) => (c.id === conversationId ? { ...c, ...conv } : c)),
      );
      void queryClient.invalidateQueries({ queryKey: CONV_LIST_KEY });
    },

    onError: (_err, _vars, ctx) => {
      if (!ctx) return;
      for (const [key, value] of ctx.snapshot) {
        queryClient.setQueryData(key, value);
      }
    },
  });
}
