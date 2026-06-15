'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

type GhlLocation = {
  location_id: string;
  location_name: string | null;
  status: string;
  merchant_id: string | null;
  company_id: string;
};

type Merchant = { id: string; name: string; slug: string };

export function InstalledLocationsList() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Record<string, string>>({});

  const locations = useQuery({
    queryKey: ['ghl', 'locations'],
    queryFn: async (): Promise<GhlLocation[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET(
        '/integrations/ghl/locations' as never,
        {} as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as { locations: GhlLocation[] }).locations;
    },
  });

  const merchants = useQuery({
    queryKey: ['merchants', 'list'],
    queryFn: async (): Promise<Merchant[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/merchants/' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant[];
    },
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['ghl', 'locations'] });

  const link = useMutation({
    mutationFn: async ({ locationId, merchantId }: { locationId: string; merchantId: string }) => {
      const api = getApiClient();
      const { error } = await api.POST(
        '/integrations/ghl/locations/{location_id}/link' as never,
        {
          params: { path: { location_id: locationId } },
          body: { merchant_id: merchantId },
        } as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
    },
    onSuccess: () => void invalidate(),
  });

  const unlink = useMutation({
    mutationFn: async (locationId: string) => {
      const api = getApiClient();
      const { error } = await api.POST(
        '/integrations/ghl/locations/{location_id}/unlink' as never,
        { params: { path: { location_id: locationId } } } as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
    },
    onSuccess: () => void invalidate(),
  });

  const rows = locations.data ?? [];
  const merchantList = merchants.data ?? [];
  const merchantName = (id: string | null) =>
    id ? (merchantList.find((m) => m.id === id)?.name ?? id) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Location installate</CardTitle>
        <p className="mt-1 text-sm text-muted-foreground">
          Ogni sub-account su cui l&apos;agenzia ha installato l&apos;app appare qui.
          Associa le location &laquo;in attesa&raquo; a un merchant per attivare il bot.
        </p>
      </CardHeader>
      <CardContent className="p-0">
        {locations.isLoading ? (
          <div className="p-6 text-sm text-muted-foreground">Caricamento location…</div>
        ) : locations.isError ? (
          <div className="p-6 text-sm text-destructive">
            Errore:{' '}
            {locations.error instanceof Error ? locations.error.message : 'sconosciuto'}
          </div>
        ) : rows.length === 0 ? (
          <div className="p-6 text-sm text-muted-foreground">
            Nessuna location installata. Installa l&apos;app sui sub-account dalla scheda
            Marketplace dopo aver collegato l&apos;agenzia.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/30 text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Location</th>
                <th className="px-4 py-3 font-medium">Stato</th>
                <th className="px-4 py-3 font-medium">Merchant</th>
                <th className="px-4 py-3 font-medium text-right">Azione</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((loc) => {
                const linked = loc.merchant_id != null;
                return (
                  <tr key={loc.location_id} className="border-b last:border-0">
                    <td className="px-4 py-3">
                      <div>{loc.location_name ?? '—'}</div>
                      <div className="font-mono text-xs text-muted-foreground">
                        {loc.location_id}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <LocationStatus status={loc.status} />
                    </td>
                    <td className="px-4 py-3">
                      {linked ? (
                        merchantName(loc.merchant_id)
                      ) : (
                        <select
                          className="rounded-md border bg-background px-2 py-1 text-sm"
                          value={selected[loc.location_id] ?? ''}
                          onChange={(e) =>
                            setSelected((s) => ({ ...s, [loc.location_id]: e.target.value }))
                          }
                        >
                          <option value="">Seleziona merchant…</option>
                          {merchantList.map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.name}
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {linked ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => unlink.mutate(loc.location_id)}
                          disabled={unlink.isPending}
                        >
                          Scollega
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          disabled={!selected[loc.location_id] || link.isPending}
                          onClick={() =>
                            link.mutate({
                              locationId: loc.location_id,
                              merchantId: selected[loc.location_id]!,
                            })
                          }
                        >
                          Collega
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

function LocationStatus({ status }: { status: string }) {
  const label =
    status === 'active'
      ? 'Attiva'
      : status === 'pending_link'
        ? 'In attesa'
        : status === 'revoked'
          ? 'Revocata'
          : status;
  const cls =
    status === 'active'
      ? 'bg-emerald-100 text-emerald-900 ring-emerald-200'
      : status === 'revoked'
        ? 'bg-destructive/10 text-destructive ring-destructive/20'
        : 'bg-muted text-muted-foreground ring-border';
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}
    >
      {label}
    </span>
  );
}
