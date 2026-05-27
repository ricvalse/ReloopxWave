'use client';

import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

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
  const query = useQuery({
    queryKey: ['objection-report'],
    queryFn: async (): Promise<ReportData> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/reports/objections' as never, {
        params: { query: { since_days: 30, samples_per_category: 3 } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      const d = data as { categories: ObjectionCategory[]; trend?: TrendCell[] };
      return { categories: d.categories ?? [], trend: d.trend ?? [] };
    },
  });

  const categories = query.data?.categories ?? [];
  const trend = query.data?.trend ?? [];
  const total = categories.reduce((acc, c) => acc + c.count, 0);
  const maxCount = Math.max(1, ...categories.map((c) => c.count));

  if (query.isLoading) {
    return <p className="text-sm text-muted-foreground">Caricamento…</p>;
  }
  if (query.error) {
    return <p className="text-sm text-destructive">Errore: {String(query.error)}</p>;
  }
  if (!categories.length) {
    return <p className="text-sm text-muted-foreground">Nessuna obiezione classificata negli ultimi 30 giorni.</p>;
  }

  return (
    <div className="space-y-4">
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
