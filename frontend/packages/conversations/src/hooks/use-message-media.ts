'use client';

import { useQuery } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';

interface MediaUrlResponse {
  url: string;
  expires_at: string;
}

/**
 * Fetch a short-lived signed URL for a message's inbound media attachment.
 *
 * Media lives in a private Supabase bucket; the backend signs the URL with the
 * service role (after an app-code merchant-prefix check), which is the only
 * approach that also works under agency→merchant impersonation (whose HS256
 * token can't sign a Storage read directly). `staleTime` sits just under the
 * server-side 1h TTL so the URL is reused within its validity window and
 * refetched afterwards. Enabled only once the download has landed
 * (`storage_path` present), so the two-phase-write placeholder never fetches.
 */
export function useMessageMedia(
  conversationId: string,
  messageId: string,
  hasStoredMedia: boolean,
) {
  const { apiBaseUrl, supabase, getAccessToken } = useConversationsContext();

  return useQuery({
    queryKey: ['message-media', conversationId, messageId],
    enabled: hasStoredMedia && !messageId.startsWith('optimistic:'),
    staleTime: 55 * 60 * 1000, // 55 min < server TTL (60 min)
    gcTime: 60 * 60 * 1000,
    retry: 1,
    queryFn: async (): Promise<string> => {
      const token = getAccessToken
        ? await getAccessToken()
        : ((await supabase.auth.getSession()).data.session?.access_token ?? null);
      if (!token) throw new Error('Sessione scaduta. Effettua il login.');

      const res = await fetch(
        `${apiBaseUrl}/conversations/${conversationId}/messages/${messageId}/media`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as MediaUrlResponse;
      return data.url;
    },
  });
}
