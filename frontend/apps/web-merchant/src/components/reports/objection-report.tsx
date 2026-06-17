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
  SkeletonCard,
  SkeletonChart,
} from '@reloop/ui';
import { MessagesSquare } from 'lucide-react';
import { getApiClient } from '@/lib/api';

const PERIOD_OPTIONS = [
  { value: 7, label: 'Ultimi 7 giorni' },
  { value: 30, label: 'Ultimi 30 giorni' },
  { value: 90, label: 'Ultimi 90 giorni' },
] as const;

type ObjectionCategory = {
  category: string;
  count: number;
  samples: {
    summary: string;
    quote: string | null;
    severity: 'low' | 'medium' | 'high' | string;
    conversation_id: string;
    created_at: string;
  }[];
};

type TrendCell = { day: string; category: string; count: number };
type ReportData = { categories: ObjectionCategory[]; trend: TrendCell[] };

export function ObjectionReport() {
  const [sinceDays, setSinceDays] = useState<number>(30);
  const [variantInput, setVariantInput] = useState('');
  const variantId = variantInput.trim();

  const query = useQuery({
    queryKey: ['objection-report', sinceDays, variantId],
    queryFn: async (): Promise<ReportData> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/reports/objections', {
        params: {
          query: {
            since_days: sinceDays,
            samples_per_category: 3,
            ...(variantId ? { variant_id: variantId } : {}),
          },
        },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      const d = data as { categories: ObjectionCategory[]; trend?: TrendCell[] };
      return { categories: d.categories ?? [], trend: d.trend ?? [] };
    },
  });

  const categories = query.data?.categories ?? [];
  const trend = query.data?.trend ?? [];
  const total = categories.reduce((acc, c) => acc + c.count, 0);
  const maxCount = Math.max(1, ...categories.map((c) => c.count));

  const filters = (
    <ObjectionFilters
      sinceDays={sinceDays}
      onSinceDaysChange={setSinceDays}
      variantInput={variantInput}
      onVariantChange={setVariantInput}
    />
  );

  if (query.isLoading) {
    return (
      <div className="space-y-4">
        {filters}
        <SkeletonCard lines={5} />
        <Card>
          <CardHeader>
            <CardTitle>Heatmap obiezioni</CardTitle>
          </CardHeader>
          <CardContent>
            <SkeletonChart />
          </CardContent>
        </Card>
        <div className="grid gap-4 md:grid-cols-2">
          <SkeletonCard />
          <SkeletonCard />
        </div>
      </div>
    );
  }
  if (query.error) {
    return (
      <div className="space-y-4">
        {filters}
        <p className="text-sm text-destructive">Errore: {String(query.error)}</p>
      </div>
    );
  }
  if (!categories.length) {
    return (
      <div className="space-y-4">
        {filters}
        <EmptyState
          icon={MessagesSquare}
          title="Nessuna obiezione classificata"
          description="Quando i clienti sollevano obiezioni nelle conversazioni, le ritrovi qui raggruppate per categoria."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {filters}
      <Card>
        <CardHeader>
          <CardTitle>Categorie ({total} obiezioni)</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {categories.map((c) => (
              <div key={c.category} className="flex items-center gap-3">
                <span className="w-32 text-sm capitalize">{c.category.replace(/_/g, ' ')}</span>
                <div className="relative h-6 flex-1 rounded bg-muted">
                  <div
                    className="h-full rounded bg-primary"
                    style={{ width: `${(c.count / maxCount) * 100}%` }}
                  />
                </div>
                <span className="w-10 text-right text-sm tabular-nums">{c.count}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <ObjectionHeatmap trend={trend} />

      <div className="grid gap-4 md:grid-cols-2">
        {categories.map((c) => (
          <Card key={c.category}>
            <CardHeader>
              <CardTitle className="capitalize">{c.category.replace(/_/g, ' ')}</CardTitle>
            </CardHeader>
            <CardContent>
              {c.samples.length === 0 ? (
                <p className="text-sm text-muted-foreground">Nessun campione.</p>
              ) : (
                <ul className="space-y-3 text-sm">
                  {c.samples.map((s, i) => (
                    <li key={i} className="border-l-2 border-muted-foreground/20 pl-3">
                      <div className="text-muted-foreground">{s.summary}</div>
                      {s.quote ? <div className="mt-1 italic">“{s.quote}”</div> : null}
                      <div className="mt-1 text-xs text-muted-foreground">
                        severità: {s.severity} · {new Date(s.created_at).toLocaleDateString()}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

function ObjectionFilters({
  sinceDays,
  onSinceDaysChange,
  variantInput,
  onVariantChange,
}: {
  sinceDays: number;
  onSinceDaysChange: (value: number) => void;
  variantInput: string;
  onVariantChange: (value: string) => void;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-4 pt-6 sm:flex-row sm:items-end">
        <div className="space-y-1.5">
          <Label htmlFor="objection-period">Periodo</Label>
          <select
            id="objection-period"
            value={sinceDays}
            onChange={(e) => onSinceDaysChange(Number(e.target.value))}
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
          <Label htmlFor="objection-variant">Variante A/B (opzionale)</Label>
          <Input
            id="objection-variant"
            value={variantInput}
            onChange={(e) => onVariantChange(e.target.value)}
            placeholder="es. A, B…"
            className="sm:w-48"
          />
        </div>
      </CardContent>
    </Card>
  );
}

function ObjectionHeatmap({ trend }: { trend: TrendCell[] }) {
  if (!trend.length) return null;

  const days = Array.from(new Set(trend.map((t) => t.day))).sort();
  const cats = Array.from(new Set(trend.map((t) => t.category))).sort();
  const counts = new Map<string, number>();
  let max = 1;
  for (const t of trend) {
    counts.set(`${t.category}|${t.day}`, t.count);
    if (t.count > max) max = t.count;
  }
  const label = (d: string) => d.slice(5); // MM-DD

  return (
    <Card>
      <CardHeader>
        <CardTitle>Heatmap obiezioni (per giorno)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="border-separate" style={{ borderSpacing: 2 }}>
            <thead>
              <tr>
                <th />
                {days.map((d) => (
                  <th
                    key={d}
                    className="px-0.5 text-[10px] font-normal text-muted-foreground"
                  >
                    {label(d)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cats.map((cat) => (
                <tr key={cat}>
                  <td className="whitespace-nowrap pr-2 text-xs capitalize">
                    {cat.replace(/_/g, ' ')}
                  </td>
                  {days.map((d) => {
                    const n = counts.get(`${cat}|${d}`) ?? 0;
                    const intensity = n === 0 ? 0 : 0.15 + 0.85 * (n / max);
                    return (
                      <td key={d} title={`${cat.replace(/_/g, ' ')} · ${d}: ${n}`}>
                        <div
                          className={`h-5 w-5 rounded-sm ${n === 0 ? 'bg-muted' : ''}`}
                          style={n === 0 ? undefined : { backgroundColor: `rgba(99,102,241,${intensity})` }}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
