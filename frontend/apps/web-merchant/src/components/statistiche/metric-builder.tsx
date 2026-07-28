'use client';

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
} from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import { STRUCTURAL_PRESETS } from './types';
import type { EventCatalogEntry, MetricDefinition, OutcomeDefinition } from './types';

/** Sceglie quali bolle mostrare, da un vocabolario — mai da testo libero.
 *
 * La tendina ha tre origini che convergono in una lista sola: i preset
 * strutturali (che il codice conosce), il catalogo eventi tipato
 * (`/analytics/event-catalog`) e le statistiche personalizzate del merchant
 * (`/statistics/outcomes`). Che il riferimento sia sempre scelto e mai digitato
 * è ciò che impedisce a una bolla di puntare a qualcosa che nessuno emette —
 * il modo esatto in cui una KPI è rimasta a zero per mesi prima di ADR 0021.
 */
export function MetricBuilder({
  definitions,
  saving,
  scopeLabel,
  onSave,
}: {
  definitions: MetricDefinition[];
  saving: boolean;
  scopeLabel: string;
  onSave: (next: MetricDefinition[]) => void;
}) {
  const { merchantId } = useMerchantId();
  const [draft, setDraft] = useState<MetricDefinition[]>(definitions);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!dirty) setDraft(definitions);
  }, [definitions, dirty]);

  const catalogQuery = useQuery({
    queryKey: ['event-catalog'],
    queryFn: async (): Promise<EventCatalogEntry[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/event-catalog', {
        params: { query: { selectable_only: true } },
      });
      if (error) throw new Error(JSON.stringify(error));
      return (data ?? []) as EventCatalogEntry[];
    },
  });

  const outcomesQuery = useQuery({
    queryKey: ['outcome-definitions', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<OutcomeDefinition[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/statistics/outcomes', {
        params: { query: { enabled_only: true } },
      });
      if (error) throw new Error(JSON.stringify(error));
      return (data ?? []) as OutcomeDefinition[];
    },
  });

  const has = (id: string) => draft.some((d) => d.id === id);
  const add = (metric: MetricDefinition) => {
    setDirty(true);
    setDraft((prev) => (prev.some((d) => d.id === metric.id) ? prev : [...prev, metric]));
  };
  const remove = (id: string) => {
    setDirty(true);
    setDraft((prev) => prev.filter((d) => d.id !== id));
  };
  const rename = (id: string, label: string) => {
    setDirty(true);
    setDraft((prev) => prev.map((d) => (d.id === id ? { ...d, label } : d)));
  };
  const move = (id: string, delta: number) => {
    setDirty(true);
    setDraft((prev) => {
      const i = prev.findIndex((d) => d.id === id);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      const moved = next[i]!;
      next[i] = next[j]!;
      next[j] = moved;
      return next;
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Scegli le bolle</CardTitle>
        <p className="text-xs text-muted-foreground">
          Stai configurando le bolle del <strong>{scopeLabel}</strong>. L&apos;ordine qui sotto è
          quello con cui appaiono in alto.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Attive</h3>
          {draft.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nessuna bolla configurata: verranno mostrate quelle di default.
            </p>
          ) : (
            <ul className="space-y-2">
              {draft.map((d, i) => (
                <li
                  key={d.id}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2"
                >
                  <Badge variant={d.source === 'outcome' ? 'default' : 'secondary'}>
                    {d.source === 'outcome' ? 'Personalizzata' : 'Automatica'}
                  </Badge>
                  <Input
                    aria-label={`Etichetta di ${d.id}`}
                    value={d.label}
                    onChange={(e) => rename(d.id, e.target.value)}
                    className="h-8 w-64"
                  />
                  <span className="text-xs text-muted-foreground">{describe(d)}</span>
                  <div className="ml-auto flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => move(d.id, -1)}
                      disabled={i === 0}
                      aria-label="Sposta su"
                    >
                      ↑
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => move(d.id, 1)}
                      disabled={i === draft.length - 1}
                      aria-label="Sposta giù"
                    >
                      ↓
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => remove(d.id)}>
                      Rimuovi
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-medium">Automatiche</h3>
          <p className="text-xs text-muted-foreground">
            Funzionano subito: il sistema registra già questi dati per ogni merchant.
          </p>
          <div className="flex flex-wrap gap-2">
            {STRUCTURAL_PRESETS.map((p) => (
              <Button
                key={p.id}
                variant="outline"
                size="sm"
                disabled={has(p.id)}
                onClick={() => add(p)}
              >
                + {p.label}
              </Button>
            ))}
            {(catalogQuery.data ?? []).map((e) => {
              const id = `event_${e.event_type.replace(/[^a-z0-9]+/g, '_')}`;
              return (
                <Button
                  key={e.event_type}
                  variant="outline"
                  size="sm"
                  disabled={has(id)}
                  title={e.description}
                  onClick={() =>
                    add({ id, label: e.label, source: 'event', event_type: e.event_type })
                  }
                >
                  + {e.label}
                </Button>
              );
            })}
          </div>
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-medium">Personalizzate</h3>
          <p className="text-xs text-muted-foreground">
            Richiedono un&apos;automazione che le registri con un nodo «Registra esito».
          </p>
          {(outcomesQuery.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nessuna statistica personalizzata. Creane una nella scheda «Statistiche
              personalizzate».
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {(outcomesQuery.data ?? []).map((o) => {
                const id = `outcome_${o.key}`;
                return (
                  <Button
                    key={o.id}
                    variant="outline"
                    size="sm"
                    disabled={has(id)}
                    onClick={() =>
                      add({ id, label: o.label, source: 'outcome', outcome_id: o.id })
                    }
                  >
                    + {o.label}
                  </Button>
                );
              })}
            </div>
          )}
        </section>

        <div className="flex items-center gap-2">
          <Button
            onClick={() => {
              onSave(draft);
              setDirty(false);
            }}
            disabled={saving || !dirty}
          >
            {saving ? 'Salvataggio…' : 'Salva'}
          </Button>
          {dirty ? (
            <Button
              variant="ghost"
              onClick={() => {
                setDraft(definitions);
                setDirty(false);
              }}
            >
              Annulla
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function describe(d: MetricDefinition): string {
  if (d.source === 'outcome') return 'esito dichiarato';
  if (d.source === 'event') return d.event_type ?? 'evento';
  const parts: string[] = [d.direction === 'in' ? 'in entrata' : 'in uscita'];
  if (d.has_reply) parts.push('con risposta');
  if (d.aggregation === 'count_unique') parts.push('conversazioni distinte');
  return parts.join(' · ');
}
