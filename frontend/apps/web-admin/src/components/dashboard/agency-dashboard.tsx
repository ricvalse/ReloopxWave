'use client';

import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { KPICard, Card, CardContent, CardHeader, CardTitle, SkeletonTable, Button } from '@reloop/ui';
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
    merchant_name?: string | null;
    leads_total: number;
    bookings_created: number;
    conversion_rate: number;
  }[];
};

export function AgencyDashboard() {
  const queryClient = useQueryClient();
  const [exportState, setExportState] = useState<'idle' | 'pending' | 'error'>('idle');

  const handleExport = async () => {
    setExportState('pending');
    try {
      const api = getApiClient();
      const { data, error } = await api.POST('/analytics/exports', {
        body: { since_days: 30 },
      });
      if (error || !data) throw new Error('request failed');
      const exportId = data.export_id;
      for (let i = 0; i < 30; i++) {
        await new Promise<void>((r) => setTimeout(r, 2000));
        const { data: dl } = await api.GET('/analytics/exports/{export_id}/download', {
          params: { path: { export_id: exportId } },
        });
        if (dl?.signed_url) {
          window.open(dl.signed_url, '_blank', 'noopener,noreferrer');
          setExportState('idle');
          return;
        }
      }
      throw new Error('timeout');
    } catch {
      setExportState('error');
      setTimeout(() => setExportState('idle'), 4000);
    }
  };

  const query = useQuery({
    queryKey: ['agency-kpis'],
    queryFn: async (): Promise<AgencyKpis> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/agency/kpis', {
        params: { query: { since_days: 30 } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as AgencyKpis;
    },
    // Realtime (below) is the primary refresh path; this is a safety-net poll
    // so the dashboard still converges if a realtime event is missed/dropped.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
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
        <KPICard label="Lead totali" loading={query.isLoading} value={k ? k.leads_total : '—'} />
        <KPICard
          label="Merchant attivi"
          loading={query.isLoading}
          value={k ? k.active_merchants : '—'}
        />
        <KPICard
          label="Messaggi ricevuti"
          loading={query.isLoading}
          value={k ? k.messages_received : '—'}
        />
        <KPICard
          label="Booking creati"
          loading={query.isLoading}
          value={k ? k.bookings_created : '—'}
        />
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Ranking merchant (conversione)</CardTitle>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleExport()}
            disabled={exportState === 'pending'}
          >
            {exportState === 'pending'
              ? 'Preparazione…'
              : exportState === 'error'
                ? 'Errore, riprova'
                : 'Esporta CSV'}
          </Button>
        </CardHeader>
        <CardContent>
          {query.isLoading ? (
            <SkeletonTable rows={5} cols={4} />
          ) : !k || k.merchants_ranking.length === 0 ? (
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
                    <td className="py-2">
                      {m.merchant_name?.trim() ? (
                        m.merchant_name
                      ) : (
                        <span className="font-mono text-xs text-muted-foreground">
                          {m.merchant_id.slice(0, 8)}…
                        </span>
                      )}
                    </td>
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
