'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle, KPICard } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { InviteUserCard } from './invite-user-card';
import { StatusBadge } from './status-badge';

type Merchant = components['schemas']['MerchantOut'];
type Kpis = components['schemas']['MerchantKpisOut'];

export function MerchantDetail({ merchantId }: { merchantId: string }) {
  const queryClient = useQueryClient();
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['merchants', merchantId],
    queryFn: async (): Promise<Merchant> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/merchants/{merchant_id}' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
  });

  const invalidateBoth = () => {
    void queryClient.invalidateQueries({ queryKey: ['merchants', merchantId] });
    void queryClient.invalidateQueries({ queryKey: ['merchants', 'list'] });
  };

  const suspend = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { data, error } = await api.POST('/merchants/{merchant_id}/suspend' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
    onSuccess: invalidateBoth,
    onError: (err) => setMutationError(err instanceof Error ? err.message : 'Errore sospensione'),
  });

  const resume = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { data, error } = await api.POST('/merchants/{merchant_id}/resume' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
    onSuccess: invalidateBoth,
    onError: (err) => setMutationError(err instanceof Error ? err.message : 'Errore riattivazione'),
  });

  const saveName = useMutation({
    mutationFn: async (name: string) => {
      const api = getApiClient();
      const { data, error } = await api.PATCH('/merchants/{merchant_id}' as never, {
        params: { path: { merchant_id: merchantId } },
        body: { name },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
    onSuccess: () => {
      setNameDraft(null);
      invalidateBoth();
    },
    onError: (err) => setMutationError(err instanceof Error ? err.message : 'Errore salvataggio'),
  });

  if (query.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento merchant…</div>;
  }

  if (query.isError || !query.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        Impossibile caricare il merchant.{' '}
        {query.error instanceof Error ? query.error.message : ''}
      </div>
    );
  }

  const m = query.data;
  const isSuspended = m.status === 'suspended';
  const pending = suspend.isPending || resume.isPending || saveName.isPending;

  return (
    <div className="space-y-4 p-6">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-3">
              {nameDraft === null ? (
                <>
                  {m.name}
                  <button
                    type="button"
                    className="text-xs font-normal text-muted-foreground hover:text-foreground"
                    onClick={() => setNameDraft(m.name)}
                  >
                    Modifica
                  </button>
                </>
              ) : (
                <form
                  className="flex items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    saveName.mutate(nameDraft);
                  }}
                >
                  <input
                    autoFocus
                    className="h-9 w-64 rounded-md border border-input bg-background px-3 text-sm"
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                  />
                  <Button type="submit" size="sm" disabled={pending || nameDraft.trim() === ''}>
                    Salva
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setNameDraft(null)}
                    disabled={pending}
                  >
                    Annulla
                  </Button>
                </form>
              )}
            </CardTitle>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{m.slug}</p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={m.status} />
            {isSuspended ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => resume.mutate()}
                disabled={pending}
              >
                Riattiva
              </Button>
            ) : (
              <Button
                size="sm"
                variant="destructive"
                onClick={() => suspend.mutate()}
                disabled={pending}
              >
                Sospendi
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm md:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">Timezone</dt>
              <dd>{m.timezone}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Locale</dt>
              <dd className="uppercase">{m.locale}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Tenant</dt>
              <dd className="font-mono text-xs">{m.tenant_id.slice(0, 8)}…</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Merchant ID</dt>
              <dd className="font-mono text-xs">{m.id.slice(0, 8)}…</dd>
            </div>
          </dl>
          {mutationError ? (
            <p className="mt-4 text-sm text-destructive">{mutationError}</p>
          ) : null}
        </CardContent>
      </Card>

      <MerchantKpiSection merchantId={merchantId} />

      <InviteUserCard merchantId={merchantId} />
    </div>
  );
}

function MerchantKpiSection({ merchantId }: { merchantId: string }) {
  const query = useQuery({
    queryKey: ['merchant-kpis', merchantId],
    queryFn: async (): Promise<Kpis> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/merchant/kpis' as never, {
        params: { query: { merchant_id: merchantId, since_days: 30 } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Kpis;
    },
  });

  const k = query.data;
  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

  return (
    <div className="space-y-4">
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
          {query.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento KPI…</p>
          ) : query.isError ? (
            <p className="text-sm text-destructive">
              Errore caricamento KPI:{' '}
              {query.error instanceof Error ? query.error.message : 'sconosciuto'}
            </p>
          ) : !k || k.score_distribution.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessun dato ancora.</p>
          ) : (
            <div className="flex h-40 items-end gap-2">
              {k.score_distribution.map((b) => {
                const bucket = b.bucket ?? 0;
                const count = b.count ?? 0;
                return (
                  <div key={bucket} className="flex flex-1 flex-col items-center">
                    <div
                      className="w-full rounded-t bg-primary/70"
                      style={{
                        height: `${Math.min(100, count * 8)}%`,
                        minHeight: 4,
                      }}
                      title={`${bucket}-${bucket + 9}: ${count}`}
                    />
                    <span className="mt-1 text-xs text-muted-foreground">{bucket}</span>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
