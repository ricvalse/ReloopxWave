'use client';

import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { KPICard, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { getBrowserSupabase } from '@/lib/supabase';

type AgencyKpis = {
  leads_total: number;
  active_merchants: number;
  messages_received: number;
  bookings_created: number;
  reminders_sent: number;
  merchants_ranking: {
    merchant_id: string;
    leads_total: number;
    bookings_created: number;
    conversion_rate: number;
  }[];
};

export function AgencyDashboard() {
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['agency-kpis'],
    queryFn: async (): Promise<AgencyKpis> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/agency/kpis' as never, {
        params: { query: { since_days: 30 } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as AgencyKpis;
    },
  });

  // UC-12 — subscribe to all analytics_events for the tenant; RLS ensures
  // the admin only receives their tenant's events.
  useEffect(() => {
    const supabase = getBrowserSupabase();
    const channel = supabase
      .channel('agency-analytics')
      .on(
        'postgres_changes' as never,
        { event: 'INSERT', schema: 'public', table: 'analytics_events' } as never,
        () => {
          queryClient.invalidateQueries({ queryKey: ['agency-kpis'] });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [queryClient]);

  const k = query.data;
  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

  return (
    <div className="space-y-4 p-6">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <KPICard label="Lead totali" value={k ? k.leads_total : '—'} />
        <KPICard label="Merchant attivi" value={k ? k.active_merchants : '—'} />
        <KPICard label="Messaggi ricevuti" value={k ? k.messages_received : '—'} />
        <KPICard label="Booking creati" value={k ? k.bookings_created : '—'} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Ranking merchant (conversione)</CardTitle>
        </CardHeader>
        <CardContent>
          {!k || k.merchants_ranking.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessun merchant ancora attivo.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-muted-foreground">
                <tr>
                  <th className="py-2">Merchant</th>
                  <th className="text-right">Lead</th>
                  <th className="text-right">Booking</th>
                  <th className="text-right">Conversion</th>
                </tr>
              </thead>
              <tbody>
                {k.merchants_ranking.map((m) => (
                  <tr key={m.merchant_id} className="border-t">
                    <td className="py-2 font-mono text-xs">{m.merchant_id.slice(0, 8)}…</td>
                    <td className="text-right">{m.leads_total}</td>
                    <td className="text-right">{m.bookings_created}</td>
                    <td className="text-right">{pct(m.conversion_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
