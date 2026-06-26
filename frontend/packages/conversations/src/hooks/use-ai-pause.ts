'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import { CONV_LIST_KEY } from './use-conversations';
import type { Conversation } from '../types';

type Snapshot = ReturnType<typeof useQueryClient>['getQueriesData'] extends (
  filters: infer _F,
) => infer R
  ? R
  : never;

/** Snapshot all list-query caches so we can roll back on error. */
function snapshotConversations(
  queryClient: ReturnType<typeof useQueryClient>,
): Snapshot {
  return queryClient.getQueriesData<Conversation[]>({ queryKey: CONV_LIST_KEY });
}

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

/** Restore snapshots taken before an optimistic update on mutation error. */
function restoreSnapshot(
  queryClient: ReturnType<typeof useQueryClient>,
  snapshot: Snapshot,
) {
  for (const [key, value] of snapshot) {
    queryClient.setQueryData(key, value);
  }
}

async function authedPost(
  supabase: ReturnType<typeof useConversationsContext>['supabase'],
  apiBaseUrl: string,
  path: string,
  body?: unknown,
  getAccessToken?: () => Promise<string | null>,
): Promise<Conversation> {
  const token = getAccessToken
    ? await getAccessToken()
    : (await supabase.auth.getSession()).data.session?.access_token ?? null;
  if (!token) throw new Error('Sessione scaduta. Effettua il login.');

  const res = await fetch(`${apiBaseUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const json = JSON.parse(text) as { detail?: string; message?: string };
      detail = json.detail ?? json.message ?? text;
    } catch {
      // use raw text
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return (await res.json()) as Conversation;
}

/** Soft-pause the bot for `hours` (auto-resumes). Optimistically patches the cache. */
export function useAiPause() {
  const { supabase, apiBaseUrl, getAccessToken } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ conversationId, hours }: { conversationId: string; hours: number }) =>
      authedPost(supabase, apiBaseUrl, `/conversations/${conversationId}/ai-pause`, { hours }, getAccessToken),
    onMutate: async ({ conversationId, hours }) => {
      const snapshot = snapshotConversations(queryClient);
      const until = new Date(Date.now() + hours * 3_600_000).toISOString();
      patchConversation(queryClient, conversationId, { ai_disabled_until: until });
      return { snapshot };
    },
    onSuccess: (conv, { conversationId }) => {
      patchConversation(queryClient, conversationId, {
        ai_disabled_until: conv.ai_disabled_until,
        assigned_to: conv.assigned_to,
      });
      void queryClient.invalidateQueries({ queryKey: CONV_LIST_KEY });
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.snapshot) restoreSnapshot(queryClient, ctx.snapshot);
    },
  });
}

/** Hand the thread back to the bot (clears the pause, re-enables auto-reply). */
export function useAiResume() {
  const { supabase, apiBaseUrl, getAccessToken } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ conversationId }: { conversationId: string }) =>
      authedPost(supabase, apiBaseUrl, `/conversations/${conversationId}/ai-resume`, undefined, getAccessToken),
    onMutate: async ({ conversationId }) => {
      const snapshot = snapshotConversations(queryClient);
      patchConversation(queryClient, conversationId, {
        ai_disabled_until: null,
        auto_reply: true,
      });
      return { snapshot };
    },
    onSuccess: (conv, { conversationId }) => {
      patchConversation(queryClient, conversationId, {
        ai_disabled_until: null,
        auto_reply: true,
        handoff_resolved_at: conv.handoff_resolved_at,
      });
      void queryClient.invalidateQueries({ queryKey: CONV_LIST_KEY });
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.snapshot) restoreSnapshot(queryClient, ctx.snapshot);
    },
  });
}
