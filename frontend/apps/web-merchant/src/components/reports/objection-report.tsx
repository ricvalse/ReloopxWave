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

export function ObjectionReport() {
  const query = useQuery({
    queryKey: ['objection-report'],
    queryFn: async (): Promise<ObjectionCategory[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/reports/objections' as never, {
        params: { query: { since_days: 30, samples_per_category: 3 } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as { categories: ObjectionCategory[] }).categories;
    },
  });

  const total = (query.data ?? []).reduce((acc, c) => acc + c.count, 0);
  const maxCount = Math.max(1, ...(query.data ?? []).map((c) => c.count));

  if (query.isLoading) {
    return <p className="text-sm text-muted-foreground">Caricamento…</p>;
  }
  if (query.error) {
    return <p className="text-sm text-destructive">Errore: {String(query.error)}</p>;
  }
  if (!query.data?.length) {
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
            {query.data.map((c) => (
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

      <div className="grid gap-4 md:grid-cols-2">
        {query.data.map((c) => (
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
