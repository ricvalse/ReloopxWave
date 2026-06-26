'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, EmptyState, SkeletonTable } from '@reloop/ui';
import { Users } from 'lucide-react';
import { getApiClient } from '@/lib/api';
import { BulkApplyDialog } from './bulk-apply-dialog';
import { StatusBadge } from './status-badge';

type Merchant = components['schemas']['MerchantOut'];

export function MerchantList() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkDialogOpen, setBulkDialogOpen] = useState(false);

  const query = useQuery({
    queryKey: ['merchants', 'list'],
    queryFn: async (): Promise<Merchant[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/merchants/');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant[];
    },
  });

  if (query.isLoading) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="p-3">
            <SkeletonTable rows={6} cols={6} />
          </CardContent>
        </Card>
      </div>
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
          <CardContent className="py-4">
            <EmptyState
              icon={Users}
              title="Nessun merchant"
              description="I merchant onboardati compariranno qui."
            />
          </CardContent>
        </Card>
      </div>
    );
  }

  const allIds = merchants.map((m) => m.id);
  const allSelected = allIds.every((id) => selected.has(id));

  function toggleAll() {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(allIds));
    }
  }

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="p-6 space-y-3">
      {/* Bulk action bar */}
      {selected.size > 0 ? (
        <div className="flex items-center gap-3 rounded-md border border-primary/20 bg-primary/5 px-4 py-2">
          <span className="text-sm font-medium">{selected.size} merchant selezionati</span>
          <Button size="sm" onClick={() => setBulkDialogOpen(true)}>
            Applica profilo...
          </Button>
          <button
            type="button"
            className="ml-auto text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setSelected(new Set())}
          >
            Deseleziona tutto
          </button>
        </div>
      ) : null}

      <Card>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/30 text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-3 w-10">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="h-4 w-4 rounded border-border"
                    aria-label="Seleziona tutti"
                  />
                </th>
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
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(m.id)}
                      onChange={() => toggleOne(m.id)}
                      className="h-4 w-4 rounded border-border"
                      aria-label={`Seleziona ${m.name}`}
                    />
                  </td>
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

      <BulkApplyDialog
        open={bulkDialogOpen}
        onClose={() => {
          setBulkDialogOpen(false);
          setSelected(new Set());
        }}
        preselectedMerchantIds={[...selected]}
      />
    </div>
  );
}
