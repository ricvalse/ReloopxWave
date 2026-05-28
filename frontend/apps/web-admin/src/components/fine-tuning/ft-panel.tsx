'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

type FtModel = {
  id: string;
  version: number;
  base_model: string;
  provider_model_id: string;
  status: string;
  is_default: boolean;
  evaluation: Record<string, unknown>;
  created_at: string | null;
};

export function FineTuningPanel() {
  const queryClient = useQueryClient();

  const models = useQuery({
    queryKey: ['ft-models'],
    queryFn: async (): Promise<FtModel[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/fine-tuning/models' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as FtModel[];
    },
    // Poll while a run is in flight (training/evaluating states change slowly).
    refetchInterval: 15_000,
  });

  const run = useMutation({
    mutationFn: async (): Promise<void> => {
      const api = getApiClient();
      const { error } = await api.POST('/fine-tuning/run' as never, {
        body: { since_days: 28, base_model: 'gpt-4.1-mini' },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ft-models'] });
    },
  });

  const fmtEval = (e: Record<string, unknown>) => {
    if (!e || Object.keys(e).length === 0) return '—';
    const b = e.baseline_score;
    const f = e.ft_score;
    if (typeof b === 'number' && typeof f === 'number') {
      return `baseline ${b} · ft ${f}${e.pass ? ' ✓' : ' ✗'}`;
    }
    return e.pass ? '✓' : '✗';
  };

  return (
    <div className="space-y-4 p-6">
      <PageHeader
        title="Fine-tuning"
        description="Avvia la pipeline (raccolta → anonimizzazione → training → valutazione → rollout A/B) e monitora i modelli per tenant."
      />

      <div className="flex items-center gap-3">
        <Button onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? 'Avvio…' : 'Avvia nuovo fine-tuning'}
        </Button>
        {run.isSuccess ? (
          <span className="text-sm text-muted-foreground">
            Pipeline avviata — segui lo stato qui sotto.
          </span>
        ) : null}
        {run.error ? (
          <span className="text-sm text-destructive">
            {run.error instanceof Error ? run.error.message : 'Errore'}
          </span>
        ) : null}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Modelli registrati</CardTitle>
        </CardHeader>
        <CardContent>
          {models.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento…</p>
          ) : (models.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nessun modello ancora. Avvia la pipeline per crearne uno.
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="py-2 font-medium">Versione</th>
                  <th className="py-2 font-medium">Base</th>
                  <th className="py-2 font-medium">Stato</th>
                  <th className="py-2 font-medium">Default</th>
                  <th className="py-2 font-medium">Valutazione</th>
                </tr>
              </thead>
              <tbody>
                {(models.data ?? []).map((m) => (
                  <tr key={m.id} className="border-t">
                    <td className="py-2">v{m.version}</td>
                    <td className="py-2 font-mono text-xs">{m.base_model}</td>
                    <td className="py-2">{m.status}</td>
                    <td className="py-2">{m.is_default ? '✓' : '—'}</td>
                    <td className="py-2 text-xs">{fmtEval(m.evaluation)}</td>
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
