'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import { LEAD_DETAIL_KEY } from './use-lead-detail';

// GDPR / DSAR operations on a lead (right of access + right to erasure). Both
// hit the backend `dsar` router (a business action, not a plain RLS read) and
// are scoped server-side by RLS to the caller's merchant. We use the same raw
// `fetch` + bearer-token pattern as `use-update-notes` rather than the generated
// client, since this package talks to the API through `apiBaseUrl` and keeps the
// supabase session as the single source of the auth token.

async function authHeader(
  supabase: ReturnType<typeof useConversationsContext>['supabase'],
): Promise<string> {
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const token = session?.access_token;
  if (!token) throw new Error('Sessione scaduta. Effettua il login.');
  return `Bearer ${token}`;
}

/** Right of access: download the lead + conversations + messages as JSON. */
export function useExportLead() {
  const { supabase, apiBaseUrl } = useConversationsContext();

  return useMutation({
    mutationFn: async (leadId: string): Promise<Record<string, unknown>> => {
      const res = await fetch(`${apiBaseUrl}/dsar/leads/${leadId}/export`, {
        method: 'GET',
        headers: { Authorization: await authHeader(supabase) },
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      return (await res.json()) as Record<string, unknown>;
    },
  });
}

/** Right to erasure: delete conversations + strip PII for a lead. */
export function useEraseLead() {
  const { supabase, apiBaseUrl } = useConversationsContext();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (
      leadId: string,
    ): Promise<{ erased: boolean; lead_id: string; conversations_deleted: number }> => {
      const res = await fetch(`${apiBaseUrl}/dsar/leads/${leadId}/erase`, {
        method: 'POST',
        headers: { Authorization: await authHeader(supabase) },
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `HTTP ${res.status}`);
      }
      return (await res.json()) as {
        erased: boolean;
        lead_id: string;
        conversations_deleted: number;
      };
    },
    onSuccess: () => {
      // The lead row is now tombstoned — refresh the detail panel.
      void queryClient.invalidateQueries({ queryKey: LEAD_DETAIL_KEY });
    },
  });
}

/** Trigger a browser download of the export payload as a JSON file. */
export function downloadJson(filename: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
