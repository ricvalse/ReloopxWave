'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

type Experiment = components['schemas']['ExperimentOut'];
type ExperimentIn = components['schemas']['ExperimentIn'];

type Metrics = {
  experiment_id: string;
  primary_metric: string;
  min_sample_size: number;
  variants: Array<{
    variant_id: string;
    assignments: number;
    events: Record<string, number>;
    primary_metric_count: number;
    rate: number;
  }>;
};

export function AbTestingPanel() {
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const list = useQuery({
    queryKey: ['ab', 'list'],
    queryFn: async (): Promise<Experiment[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/ab-test/' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Experiment[];
    },
  });

  const start = useMutation({
    mutationFn: async (experimentId: string): Promise<Experiment> => {
      const api = getApiClient();
      const { data, error } = await api.POST('/ab-test/{experiment_id}/start' as never, {
        params: { path: { experiment_id: experimentId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Experiment;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ab', 'list'] });
    },
  });

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between gap-4">
        <div className="text-sm text-muted-foreground">
          UC-09 — assegnazione sticky per lead (stessa conversazione → stessa variante).
        </div>
        <Button
          onClick={() => setCreating((v) => !v)}
          variant={creating ? 'outline' : 'default'}
        >
          {creating ? 'Annulla' : '+ Nuovo esperimento'}
        </Button>
      </div>

      {creating ? (
        <CreateExperimentForm
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            void queryClient.invalidateQueries({ queryKey: ['ab', 'list'] });
          }}
        />
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Esperimenti attivi</CardTitle>
        </CardHeader>
        <CardContent>
          {list.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento…</p>
          ) : list.isError ? (
            <p className="text-sm text-destructive">
              {list.error instanceof Error ? list.error.message : 'Errore'}
            </p>
          ) : (list.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessun esperimento creato.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="py-2 font-medium">Nome</th>
                  <th className="py-2 font-medium">Varianti</th>
                  <th className="py-2 font-medium">Metrica</th>
                  <th className="py-2 font-medium">Stato</th>
                  <th className="py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {(list.data ?? []).map((e) => (
                  <tr key={e.id} className="border-t">
                    <td className="py-2 font-medium">{e.name}</td>
                    <td className="py-2">
                      {(e.variants as Array<{ id?: string; weight?: number }>)
                        .map((v) => `${v.id ?? '?'} (${v.weight ?? 0}%)`)
                        .join(' vs ')}
                    </td>
                    <td className="py-2 font-mono text-xs">{e.primary_metric}</td>
                    <td className="py-2">{e.status}</td>
                    <td className="py-2 space-x-2 text-right">
                      {e.status !== 'running' ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => start.mutate(e.id)}
                          disabled={start.isPending}
                        >
                          Avvia
                        </Button>
                      ) : null}
                      <Button
                        size="sm"
                        variant={selected === e.id ? 'default' : 'ghost'}
                        onClick={() => setSelected(selected === e.id ? null : e.id)}
                      >
                        Metriche
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      {selected ? <MetricsCard experimentId={selected} /> : null}
    </div>
  );
}

function CreateExperimentForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [weightA, setWeightA] = useState(50);
  const [primaryMetric, setPrimaryMetric] = useState('booking.created');

  const create = useMutation({
    mutationFn: async (): Promise<Experiment> => {
      const payload: ExperimentIn = {
        name,
        description: description || null,
        variants: [
          { id: 'control', weight: weightA, prompt_template_id: null },
          { id: 'variant', weight: 100 - weightA, prompt_template_id: null },
        ],
        primary_metric: primaryMetric,
        min_sample_size: 100,
      };
      const api = getApiClient();
      const { data, error } = await api.POST('/ab-test/' as never, { body: payload } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Experiment;
    },
    onSuccess: onCreated,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Nuovo esperimento</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="ab-name">
                Nome
              </label>
              <input
                id="ab-name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="ab-metric">
                Metrica primaria
              </label>
              <input
                id="ab-metric"
                required
                value={primaryMetric}
                onChange={(e) => setPrimaryMetric(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
              />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-sm font-medium" htmlFor="ab-desc">
              Descrizione
            </label>
            <input
              id="ab-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          </div>
          <div className="space-y-1">
            <label className="text-sm font-medium" htmlFor="ab-weight">
              Split control / variant: {weightA}% / {100 - weightA}%
            </label>
            <input
              id="ab-weight"
              type="range"
              min={0}
              max={100}
              step={5}
              value={weightA}
              onChange={(e) => setWeightA(Number(e.target.value))}
              className="w-full"
            />
          </div>
          {create.error ? (
            <p className="text-sm text-destructive">
              {create.error instanceof Error ? create.error.message : 'Errore'}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={create.isPending}>
              Annulla
            </Button>
            <Button type="submit" disabled={create.isPending || !name}>
              {create.isPending ? 'Creazione…' : 'Crea esperimento'}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function MetricsCard({ experimentId }: { experimentId: string }) {
  const metrics = useQuery({
    queryKey: ['ab', 'metrics', experimentId],
    queryFn: async (): Promise<Metrics> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/ab-test/{experiment_id}/metrics' as never, {
        params: { path: { experiment_id: experimentId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Metrics;
    },
    refetchInterval: 15_000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Metriche (refresh ogni 15s)</CardTitle>
      </CardHeader>
      <CardContent>
        {metrics.isLoading ? (
          <p className="text-sm text-muted-foreground">Caricamento…</p>
        ) : metrics.isError ? (
          <p className="text-sm text-destructive">
            {metrics.error instanceof Error ? metrics.error.message : 'Errore'}
          </p>
        ) : metrics.data ? (
          <>
            <p className="mb-3 text-sm text-muted-foreground">
              Metrica primaria:{' '}
              <code className="font-mono text-xs">{metrics.data.primary_metric}</code> · Min sample:{' '}
              {metrics.data.min_sample_size}
            </p>
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="py-2 font-medium">Variante</th>
                  <th className="py-2 font-medium">Assegnazioni</th>
                  <th className="py-2 font-medium">Eventi target</th>
                  <th className="py-2 font-medium">Rate</th>
                  <th className="py-2 font-medium">Sample OK</th>
                </tr>
              </thead>
              <tbody>
                {metrics.data.variants.map((v) => (
                  <tr key={v.variant_id} className="border-t">
                    <td className="py-2 font-medium">{v.variant_id}</td>
                    <td className="py-2">{v.assignments}</td>
                    <td className="py-2">{v.primary_metric_count}</td>
                    <td className="py-2">{(v.rate * 100).toFixed(2)}%</td>
                    <td className="py-2">
                      {v.assignments >= metrics.data!.min_sample_size ? '✓' : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
