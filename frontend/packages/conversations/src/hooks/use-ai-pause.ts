'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import { CONV_LIST_KEY } from './use-conversations';
import type { Conversation } from '../types';

/** Patch the cached conversation rows in every list query after a pause/resume. */
function patchConversation(
  queryClient: ReturnType<typeof useQueryClient>,
  id: string,
  patch: Partial<Conversation>,
) {
  queryClient.setQueriesData<Conversation[]>({ queryKey: CONV_LIST_KEY }, (old) =>
    old?.map((c) => (c.id === id ? { ...c, ...patch } : c)),
  );
}

async function authedPost(
  supabase: ReturnType<typeof useConversationsContext>['supabase'],
  apiBaseUrl: string,
  path: string,
  body?: unknown,
): Promise<Conversation> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;
  if (!token) throw new Error('Sessione scaduta. Effettua il login.');

  const res = await fetch(`${apiBaseUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
  return (await res.json()) as Conversation;
}

/** Soft-pause the bot for `hours` (auto-resumes). Optimistically patches the cache. */
export function useAiPause() {
  const { supabase, apiBaseUrl } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ conversationId, hours }: { conversationId: string; hours: number }) =>
      authedPost(supabase, apiBaseUrl, `/conversations/${conversationId}/ai-pause`, { hours }),
    onSuccess: (conv) => {
      patchConversation(queryClient, conv.id, {
        ai_disabled_until: conv.ai_disabled_until,
        assigned_to: conv.assigned_to,
      });
    },
  });
}

/** Hand the thread back to the bot (clears the pause, re-enables auto-reply). */
export function useAiResume() {
  const { supabase, apiBaseUrl } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ conversationId }: { conversationId: string }) =>
      authedPost(supabase, apiBaseUrl, `/conversations/${conversationId}/ai-resume`),
    onSuccess: (conv) => {
      patchConversation(queryClient, conv.id, {
        ai_disabled_until: null,
        auto_reply: true,
        handoff_resolved_at: conv.handoff_resolved_at,
      });
    },
  });
}
