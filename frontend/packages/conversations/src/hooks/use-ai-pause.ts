'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
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

/** POST to a conversation endpoint with the caller's Supabase JWT. Shared with
 *  the bot takeover switch (`use-toggle-auto-reply`), which hits the same
 *  ai-resume / ai-takeover pair. Generic over the response body: every
 *  per-conversation endpoint returns the updated `Conversation` (the default),
 *  but a merchant-wide action like `ai-resume-bulk` returns its own summary
 *  shape instead. */
export async function authedConversationPost<T = Conversation>(
  supabase: ReturnType<typeof useConversationsContext>['supabase'],
  apiBaseUrl: string,
  path: string,
  body?: unknown,
  getAccessToken?: () => Promise<string | null>,
): Promise<T> {
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
  return (await res.json()) as T;
}

/** Soft-pause the bot for `hours` (auto-resumes). Optimistically patches the cache. */
export function useAiPause() {
  const { supabase, apiBaseUrl, getAccessToken } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ conversationId, hours }: { conversationId: string; hours: number }) =>
      authedConversationPost(supabase, apiBaseUrl, `/conversations/${conversationId}/ai-pause`, { hours }, getAccessToken),
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
      authedConversationPost(supabase, apiBaseUrl, `/conversations/${conversationId}/ai-resume`, undefined, getAccessToken),
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

interface ResumeBulkResult {
  resumed: string[];
  count: number;
}

/** Hand every soft-paused thread back to the bot in one call ("Riattiva
 *  tutte"). Deliberately narrower than the per-conversation resume: the
 *  backend only clears threads with no handoff record at all — a real
 *  human handoff still needs someone to open that thread and resolve it
 *  on purpose. No optimistic patch (the affected set isn't known until the
 *  response comes back); the list just refetches on success. */
export function useAiResumeBulk() {
  const { supabase, apiBaseUrl, getAccessToken } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () =>
      authedConversationPost<ResumeBulkResult>(
        supabase,
        apiBaseUrl,
        '/conversations/ai-resume-bulk',
        undefined,
        getAccessToken,
      ),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: CONV_LIST_KEY });
      toast.success(
        result.count > 0
          ? `${result.count} conversazion${result.count === 1 ? 'e riattivata' : 'i riattivate'}`
          : 'Nessuna conversazione in pausa da riattivare',
      );
    },
    onError: (err) => {
      toast.error('Impossibile riattivare le conversazioni', {
        description: (err as Error).message ?? 'Errore sconosciuto',
      });
    },
  });
}
