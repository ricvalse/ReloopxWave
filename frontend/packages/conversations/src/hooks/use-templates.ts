'use client';

import { useQuery } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';

/** Minimal shape of an approved WhatsApp template needed by the composer picker.
 *  Mirrors `WhatsAppTemplateOut` (only the fields the picker renders/uses). */
export interface WhatsAppTemplate {
  id: string;
  name: string;
  language: string;
  status: string;
  body: string;
  /** Ordered placeholder names ({{1}}, {{2}}, …) the agent fills before sending. */
  variables: string[];
}

export function templatesQueryKey() {
  return ['whatsapp-templates', 'approved'] as const;
}

/**
 * Fetch the merchant's approved WhatsApp templates (CC-WA): the composer offers
 * these when the 24h free-text window is closed, since only an approved template
 * is deliverable out-of-window.
 */
export function useApprovedTemplates(enabled = true) {
  const { supabase, apiBaseUrl, getAccessToken } = useConversationsContext();

  return useQuery({
    queryKey: templatesQueryKey(),
    enabled,
    staleTime: 60_000,
    queryFn: async (): Promise<WhatsAppTemplate[]> => {
      const token = getAccessToken
        ? await getAccessToken()
        : (await supabase.auth.getSession()).data.session?.access_token ?? null;
      if (!token) throw new Error('Sessione scaduta. Effettua il login.');

      const res = await fetch(`${apiBaseUrl}/whatsapp-templates?status=approved`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      const rows = (await res.json()) as WhatsAppTemplate[];
      // Defensive: keep only approved even if the filter is ignored upstream.
      return rows.filter((t) => t.status === 'approved');
    },
  });
}
