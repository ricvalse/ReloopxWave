'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Card, CardContent } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { StatusBadge } from './status-badge';

type Merchant = components['schemas']['MerchantOut'];

export function MerchantList() {
  const query = useQuery({
    queryKey: ['merchants', 'list'],
    queryFn: async (): Promise<Merchant[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/merchants/' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant[];
    },
  });

  if (query.isLoading) {
    return (
      <div className="p-6 text-sm text-muted-foreground">Caricamento merchant…</div>
    );
  }

  if (query.isError) {
    return (
      <div className="p-6 text-sm text-destructive">
        Errore nel caricamento: {query.error instanceof Error ? query.error.message : 'sconosciuto'}
      </div>
    );
  }

  const merchants = query.data ?? [];

  if (merchants.length === 0) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Nessun merchant ancora onboardato.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6">
      <Card>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/30 text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Nome</th>
                <th className="px-4 py-3 font-medium">Slug</th>
                <th className="px-4 py-3 font-medium">Timezone</th>
                <th className="px-4 py-3 font-medium">Locale</th>
                <th className="px-4 py-3 font-medium">Stato</th>
                <th className="px-4 py-3 font-medium" aria-label="Azioni" />
              </tr>
            </thead>
            <tbody>
              {merchants.map((m) => (
                <tr key={m.id} className="border-t">
                  <td className="px-4 py-3 font-medium">{m.name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{m.slug}</td>
                  <td className="px-4 py-3">{m.timezone}</td>
                  <td className="px-4 py-3 uppercase">{m.locale}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={m.status} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/merchants/${m.id}`}
                      className="text-primary hover:underline"
                    >
                      Dettagli →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
