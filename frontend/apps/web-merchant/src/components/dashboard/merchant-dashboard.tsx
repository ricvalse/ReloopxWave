'use client';

import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { KPICard, Card, CardContent, CardHeader, CardTitle, SkeletonChart } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { getBrowserSupabase } from '@/lib/supabase';
import { useMerchantId } from '@/hooks/use-merchant-id';
import { SetupChecklist } from '@/components/dashboard/setup-checklist';

type Kpis = {
  leads_total: number;
  leads_hot: number;
  messages_received: number;
  messages_replied: number;
  response_rate: number;
  bookings_created: number;
  booking_rate: number;
  reminders_sent: number;
  score_distribution: { bucket: number; count: number }[];
};

const PERIODS = [
  { value: 7, label: 'Ultimi 7 giorni' },
  { value: 30, label: 'Ultimi 30 giorni' },
  { value: 90, label: 'Ultimi 90 giorni' },
];

export function MerchantDashboard() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [sinceDays, setSinceDays] = useState(30);
  const [campaign, setCampaign] = useState('');

  const campaignsQuery = useQuery({
    queryKey: ['merchant-campaigns', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<string[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/merchant/campaigns');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data ?? [];
    },
  });

  const query = useQuery({
    queryKey: ['merchant-kpis', merchantId, sinceDays, campaign],
    queryFn: async (): Promise<Kpis> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/merchant/kpis', {
        params: {
          query: { since_days: sinceDays, ...(campaign ? { campaign } : {}) },
        },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Kpis;
    },
    // Realtime (below) is the primary refresh path; this is a safety-net poll
    // so the dashboard still converges if a realtime event is missed/dropped.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });

  // UC-11 — Supabase Realtime: invalidate on new analytics_events for this merchant.
  useEffect(() => {
    if (!merchantId) return;
    const supabase = getBrowserSupabase();
    const channel = supabase
      .channel(`analytics:${merchantId}`)
      .on(
        'postgres_changes' as never,
        {
          event: 'INSERT',
          schema: 'public',
          table: 'analytics_events',
          filter: `merchant_id=eq.${merchantId}`,
        } as never,
        () => {
          queryClient.invalidateQueries({ queryKey: ['merchant-kpis', merchantId] });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [merchantId, queryClient]);

  const k = query.data;
  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

  return (
    <div className="space-y-4 p-6">
      <SetupChecklist />
      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label="Periodo"
          value={sinceDays}
          onChange={(e) => setSinceDays(Number(e.target.value))}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Campagna"
          value={campaign}
          onChange={(e) => setCampaign(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          disabled={(campaignsQuery.data ?? []).length === 0}
        >
          <option value="">Tutte le campagne</option>
          {(campaignsQuery.data ?? []).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <KPICard label="Lead totali" loading={query.isLoading} value={k ? k.leads_total : '—'} />
        <KPICard label="Lead hot" loading={query.isLoading} value={k ? k.leads_hot : '—'} />
        <KPICard
          label="Tasso risposta"
          loading={query.isLoading}
          value={k ? pct(k.response_rate) : '—'}
        />
        <KPICard
          label="Booking rate"
          loading={query.isLoading}
          value={k ? pct(k.booking_rate) : '—'}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Distribuzione score lead</CardTitle>
        </CardHeader>
        <CardContent>
          {query.isLoading ? (
            <SkeletonChart />
          ) : !k || k.score_distribution.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessun dato ancora.</p>
          ) : (
            <div className="flex items-end gap-2 h-40">
              {k.score_distribution.map((b) => (
                <div key={b.bucket} className="flex flex-1 flex-col items-center">
                  <div
                    className="w-full rounded-t bg-primary/70"
                    style={{
                      height: `${Math.min(100, b.count * 8)}%`,
                      minHeight: 4,
                    }}
                    title={`${b.bucket}-${b.bucket + 9}: ${b.count}`}
                  />
                  <span className="mt-1 text-xs text-muted-foreground">{b.bucket}</span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="text-xs text-muted-foreground">
        Aggiornamento in tempo reale via Supabase Realtime, con refresh automatico ogni 30s come rete di sicurezza.
      </div>
    </div>
  );
}
