'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import { LEAD_DETAIL_KEY } from './use-lead-detail';

interface UpdateNoteArgs {
  conversationId: string;
  /** Trimmed note text, or null/empty to clear it. */
  note: string | null;
}

/**
 * Save the agent's internal note via the backend
 * `PATCH /conversations/{id}/notes` endpoint (a business action, not a plain
 * RLS write — it normalises empty→NULL and is the audited write path).
 *
 * Immediate UX is handled locally by the notes editor (its textarea reflects
 * the keystrokes and shows a save indicator), so this mutation doesn't need an
 * optimistic cache patch. On success we invalidate the detail query so any
 * re-open / other open panel re-reads the canonical note.
 */
export function useUpdateNotes() {
  const { supabase, apiBaseUrl, getAccessToken } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ conversationId, note }: UpdateNoteArgs) => {
      const token = getAccessToken
        ? await getAccessToken()
        : (await supabase.auth.getSession()).data.session?.access_token ?? null;
      if (!token) throw new Error('Sessione scaduta. Effettua il login.');

      const res = await fetch(`${apiBaseUrl}/conversations/${conversationId}/notes`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ internal_note: note }),
      });

      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      return (await res.json()) as { id: string; internal_note: string | null };
    },

    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LEAD_DETAIL_KEY });
    },
  });
}
