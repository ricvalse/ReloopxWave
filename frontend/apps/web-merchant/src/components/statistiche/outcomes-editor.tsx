'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  toast,
} from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import type { OutcomeDefinition } from './types';

/** Come viene accertato un esito. La qualità del dato cambia parecchio, e la
 *  differenza resta visibile sulla bolla ("di cui N verificati"). */
const SOURCE_KINDS = [
  {
    value: 'ai_check',
    label: 'L’AI riconosce la risposta in chat',
    hint: 'Dedotto dal testo: veloce da attivare, ma è una dichiarazione del lead, non un fatto.',
  },
  {
    value: 'webhook',
    label: 'Un webhook esterno lo conferma',
    hint: 'Il più affidabile: se il questionario ha un link tracciato, usa questo.',
  },
  {
    value: 'manual',
    label: 'Lo marca un operatore',
    hint: 'Verificato da una persona, dall’inbox.',
  },
];

const CARDINALITIES = [
  { value: 'once_per_lead', label: 'Una volta per lead', hint: 'Il caso normale (es. un questionario).' },
  { value: 'once_per_conversation', label: 'Una volta per conversazione' },
  { value: 'repeatable', label: 'Ripetibile', hint: 'Ogni occorrenza conta.' },
];

export function OutcomesEditor() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [label, setLabel] = useState('');
  const [key, setKey] = useState('');
  const [sourceKind, setSourceKind] = useState('ai_check');
  const [cardinality, setCardinality] = useState('once_per_lead');

  const query = useQuery({
    queryKey: ['outcome-definitions', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<OutcomeDefinition[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/statistics/outcomes');
      if (error) throw new Error(JSON.stringify(error));
      return (data ?? []) as OutcomeDefinition[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { error } = await api.POST('/statistics/outcomes', {
        body: {
          key: key || slugify(label),
          label,
          source_kind: sourceKind,
          cardinality,
        },
      });
      if (error) throw new Error(JSON.stringify(error));
    },
    onSuccess: () => {
      toast.success('Statistica creata. Ora cablala in un’automazione.');
      setLabel('');
      setKey('');
      void queryClient.invalidateQueries({ queryKey: ['outcome-definitions'] });
    },
    onError: () => toast.error('Creazione non riuscita'),
  });

  const toggleMutation = useMutation({
    mutationFn: async (row: OutcomeDefinition) => {
      const api = getApiClient();
      const { error } = await api.PATCH('/statistics/outcomes/{outcome_id}', {
        params: { path: { outcome_id: row.id } },
        body: { enabled: !row.enabled },
      });
      if (error) throw new Error(JSON.stringify(error));
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['outcome-definitions'] }),
    onError: () => toast.error('Aggiornamento non riuscito'),
  });

  const rows = query.data ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Crea una statistica personalizzata</CardTitle>
          <p className="text-xs text-muted-foreground">
            Prima la dichiari qui, poi la cabli in un&apos;automazione con il nodo «Registra
            esito». Sono due passaggi separati di proposito: così il nodo la sceglie da una
            tendina e non può puntare a una statistica che non esiste.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="outcome-label">Nome</Label>
              <Input
                id="outcome-label"
                value={label}
                placeholder="Ha compilato il questionario"
                onChange={(e) => setLabel(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Puoi rinominarlo quando vuoi senza perdere lo storico.
              </p>
            </div>
            <div className="space-y-1">
              <Label htmlFor="outcome-key">Identificativo</Label>
              <Input
                id="outcome-key"
                value={key || slugify(label)}
                onChange={(e) => setKey(slugify(e.target.value))}
              />
              <p className="text-xs text-muted-foreground">
                Stabile: non cambia più dopo la creazione.
              </p>
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="outcome-source">Come viene accertato</Label>
            <select
              id="outcome-source"
              value={sourceKind}
              onChange={(e) => setSourceKind(e.target.value)}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              {SOURCE_KINDS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              {SOURCE_KINDS.find((s) => s.value === sourceKind)?.hint}
            </p>
          </div>

          <div className="space-y-1">
            <Label htmlFor="outcome-cardinality">Quante volte può accadere</Label>
            <select
              id="outcome-cardinality"
              value={cardinality}
              onChange={(e) => setCardinality(e.target.value)}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              {CARDINALITIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              {CARDINALITIES.find((c) => c.value === cardinality)?.hint ?? ''} Non è modificabile
              dopo la creazione.
            </p>
          </div>

          <Button
            onClick={() => createMutation.mutate()}
            disabled={!label.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? 'Creazione…' : 'Crea'}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Statistiche esistenti</CardTitle>
        </CardHeader>
        <CardContent>
          {query.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento…</p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessuna statistica personalizzata.</p>
          ) : (
            <ul className="space-y-2">
              {rows.map((r) => (
                <li
                  key={r.id}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2"
                >
                  <span className="font-medium">{r.label}</span>
                  <code className="text-xs text-muted-foreground">{r.key}</code>
                  {r.is_library ? <Badge variant="secondary">Libreria agenzia</Badge> : null}
                  {!r.enabled ? <Badge variant="outline">Disattivata</Badge> : null}
                  <span className="text-xs text-muted-foreground">
                    {SOURCE_KINDS.find((s) => s.value === r.source_kind)?.label ?? r.source_kind}
                  </span>
                  {!r.is_library ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="ml-auto"
                      onClick={() => toggleMutation.mutate(r)}
                    >
                      {r.enabled ? 'Disattiva' : 'Riattiva'}
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-muted-foreground">
            Disattivare smette di misurare e conserva lo storico. Eliminare cancella anche i dati
            raccolti, quindi non è offerto qui.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 64);
}
