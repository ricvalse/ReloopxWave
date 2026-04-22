'use client';

import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { KPICard, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { getBrowserSupabase } from '@/lib/supabase';
import { useMerchantId } from '@/hooks/use-merchant-id';

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

export function MerchantDashboard() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ['merchant-kpis', merchantId],
    queryFn: async (): Promise<Kpis> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/merchant/kpis' as never, {
        params: { query: { since_days: 30 } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Kpis;
    },
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
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <KPICard label="Lead totali" value={k ? k.leads_total : '—'} />
        <KPICard label="Lead hot" value={k ? k.leads_hot : '—'} />
        <KPICard label="Tasso risposta" value={k ? pct(k.response_rate) : '—'} />
        <KPICard label="Booking rate" value={k ? pct(k.booking_rate) : '—'} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Distribuzione score lead</CardTitle>
        </CardHeader>
        <CardContent>
          {!k || k.score_distribution.length === 0 ? (
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
        Attivo Supabase Realtime: questa dashboard si aggiorna sola quando arrivano nuovi eventi.
      </div>
    </div>
  );
}
