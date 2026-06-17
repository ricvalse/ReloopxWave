'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Input,
  Label,
  PageHeader,
  SkeletonCard,
} from '@reloop/ui';
import { MessagesSquare } from 'lucide-react';
import { getApiClient } from '@/lib/api';

type ObjectionCategory = { category: string; count: number };
type ReportData = { categories: ObjectionCategory[] };

const PERIOD_OPTIONS = [
  { value: 7, label: 'Ultimi 7 giorni' },
  { value: 30, label: 'Ultimi 30 giorni' },
  { value: 90, label: 'Ultimi 90 giorni' },
] as const;

export function AgencyObjectionReport() {
  const [sinceDays, setSinceDays] = useState<number>(30);
  const [variantInput, setVariantInput] = useState('');
  const variantId = variantInput.trim();

  const query = useQuery({
    queryKey: ['agency-objection-report', sinceDays, variantId],
    queryFn: async (): Promise<ReportData> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/reports/objections/agency', {
        params: {
          query: {
            since_days: sinceDays,
            ...(variantId ? { variant_id: variantId } : {}),
          },
        },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      const d = data as { categories?: ObjectionCategory[] };
      return { categories: d.categories ?? [] };
    },
  });

  const categories = query.data?.categories ?? [];
  const total = categories.reduce((acc, c) => acc + c.count, 0);
  const maxCount = Math.max(1, ...categories.map((c) => c.count));

  const filters = (
    <Card>
      <CardContent className="flex flex-col gap-4 pt-6 sm:flex-row sm:items-end">
        <div className="space-y-1.5">
          <Label htmlFor="agency-objection-period">Periodo</Label>
          <select
            id="agency-objection-period"
            value={sinceDays}
            onChange={(e) => setSinceDays(Number(e.target.value))}
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 sm:w-48"
          >
            {PERIOD_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="agency-objection-variant">Variante A/B (opzionale)</Label>
          <Input
            id="agency-objection-variant"
            value={variantInput}
            onChange={(e) => setVariantInput(e.target.value)}
            placeholder="es. A, B…"
            className="sm:w-48"
          />
        </div>
      </CardContent>
    </Card>
  );

  return (
    <>
      <PageHeader
        title="Obiezioni (agenzia)"
        description="UC-13 — categorie di obiezioni aggregate su tutti i merchant dell'agenzia."
      />
      <div className="space-y-4 p-6">
        {filters}

        {query.isLoading ? (
          <>
            <SkeletonCard lines={6} />
            <SkeletonCard lines={4} />
          </>
        ) : query.error ? (
          <p className="text-sm text-destructive">Errore: {String(query.error)}</p>
        ) : !categories.length ? (
          <EmptyState
            icon={MessagesSquare}
            title="Nessuna obiezione classificata"
            description="Quando i merchant accumulano obiezioni nelle conversazioni, le ritrovi qui aggregate per categoria."
          />
        ) : (
          <Card>
            <CardHeader>
              <CardTitle>Categorie ({total} obiezioni)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {categories.map((c) => (
                  <div key={c.category} className="flex items-center gap-3">
                    <span className="w-40 text-sm capitalize">
                      {c.category.replace(/_/g, ' ')}
                    </span>
                    <div className="relative h-6 flex-1 rounded bg-muted">
                      <div
                        className="h-full rounded bg-primary"
                        style={{ width: `${(c.count / maxCount) * 100}%` }}
                      />
                    </div>
                    <span className="w-12 text-right text-sm tabular-nums">{c.count}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </>
  );
}
