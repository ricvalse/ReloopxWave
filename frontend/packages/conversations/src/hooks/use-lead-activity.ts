'use client';

import { useQuery } from '@tanstack/react-query';
import { useConversationsContext } from '../lib/context';

export interface ActivityEvent {
  id: string;
  event_type: string;
  subject_type: string | null;
  subject_id: string | null;
  properties: Record<string, unknown>;
  occurred_at: string;
}

export function useLeadActivity(leadId: string | null | undefined) {
  const { supabase } = useConversationsContext();

  return useQuery({
    queryKey: ['conversations', 'lead-activity', leadId ?? null],
    enabled: Boolean(leadId),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    queryFn: async (): Promise<ActivityEvent[]> => {
      if (!leadId) return [];
      const { data, error } = await supabase
        .from('analytics_events')
        .select('id, event_type, subject_type, subject_id, properties, occurred_at')
        .eq('subject_id', leadId)
        .order('occurred_at', { ascending: false })
        .limit(50);
      if (error) throw error;
      return (data ?? []) as ActivityEvent[];
    },
  });
}
