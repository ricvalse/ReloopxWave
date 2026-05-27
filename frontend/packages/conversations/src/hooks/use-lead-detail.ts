'use client';

import { useQuery } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';
import type { Lead, Objection } from '../types';

/** Query-key prefix for the detail-panel reads (lead + objections). */
export const LEAD_DETAIL_KEY = ['conversations', 'lead-detail'] as const;

// `leads` and `objections` are NOT in the supabase_realtime publication (only
// messages + conversations are — migration 0008), and they change slowly: the
// score/sentiment/objections are written by background workers, not by the
// agent. So this hook leans on a gentle poll rather than a live subscription,
// matching the 30s circuit-breaker cadence used elsewhere in the package.
const POLL_MS = 30_000;

export interface LeadDetail {
  lead: Lead | null;
  objections: Objection[];
  /** Conversation internal note. Read best-effort — see note-read below. */
  note: string | null;
}

/**
 * Read the lead linked to a conversation plus its detected objections, both
 * directly from Supabase under RLS (the convention for protected reads). A
 * conversation may have no lead yet (`leadId` null) — the hook stays disabled
 * for the lead read but still pulls objections, which are keyed by
 * conversation, not lead.
 */
export function useLeadDetail(conversationId: string | null, leadId: string | null | undefined) {
  const { supabase } = useConversationsContext();

  return useQuery({
    queryKey: [...LEAD_DETAIL_KEY, conversationId, leadId ?? null],
    enabled: Boolean(conversationId),
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
    queryFn: async (): Promise<LeadDetail> => {
      const [leadRes, objRes] = await Promise.all([
        leadId
          ? supabase
              .from('leads')
              .select(
                'id, name, email, phone, score, score_reasons, sentiment, status, pipeline_stage_id, meta',
              )
              .eq('id', leadId)
              .maybeSingle()
          : Promise.resolve({ data: null, error: null }),
        supabase
          .from('objections')
          .select('id, category, summary, quote, severity, created_at')
          .eq('conversation_id', conversationId!)
          .order('created_at', { ascending: false }),
      ]);

      if (leadRes.error) throw leadRes.error;
      if (objRes.error) throw objRes.error;

      // The note read is best-effort and isolated from the lead/objection reads
      // above: `conversations.internal_note` ships in migration 0012, so until
      // that migration is applied the column won't exist. We tolerate that here
      // (note → null) so the rest of the panel works against an un-migrated DB;
      // the feature lights up automatically once the column exists.
      let note: string | null = null;
      try {
        const noteRes = await supabase
          .from('conversations')
          .select('internal_note')
          .eq('id', conversationId!)
          .maybeSingle();
        if (!noteRes.error) {
          note = ((noteRes.data as { internal_note?: string | null } | null)?.internal_note ??
            null) as string | null;
        }
      } catch {
        // column not present yet — notes inert until migration 0012 lands
      }

      return {
        lead: (leadRes.data ?? null) as unknown as Lead | null,
        objections: (objRes.data ?? []) as unknown as Objection[],
        note,
      };
    },
  });
}
