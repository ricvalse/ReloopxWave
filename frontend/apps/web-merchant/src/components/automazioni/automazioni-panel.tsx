'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { Copy } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { AutomationEditor } from './automation-editor';
import { TRIGGER_DEFS } from './automation-nodes';

type Automation = components['schemas']['AutomationOut'];

// Italian label per trigger type, derived from the canvas trigger definitions.
const TRIGGER_LABEL: Record<string, string> = Object.fromEntries(
  TRIGGER_DEFS.map((d) => [d.type, d.label]),
);

const NAME_MAX = 200; // AutomationUpsertIn.name

// "Promo estate" → "Promo estate (copia)", poi "(copia 2)", "(copia 3)"… Il suffisso
// viene tolto prima di riapplicarlo, così duplicare una copia non produce
// "(copia) (copia)". I nomi non sono unici a DB: è solo leggibilità in lista.
function copyName(base: string, existing: readonly string[]): string {
  const root = base.replace(/\s*\(copia(?: \d+)?\)$/i, '').trim() || base;
  const taken = new Set(existing);
  for (let i = 1; ; i += 1) {
    const suffix = i === 1 ? ' (copia)' : ` (copia ${i})`;
    const candidate = root.slice(0, NAME_MAX - suffix.length) + suffix;
    if (!taken.has(candidate)) return candidate;
  }
}

// AutomationOut → AutomationUpsertIn: il grafo arriva già completo dalla lista,
// quindi la copia non richiede una fetch aggiuntiva. La copia nasce SEMPRE come
// bozza: clonare un'automazione attiva la farebbe partire in parallelo
// all'originale sullo stesso trigger (doppio invio al lead).
function duplicatePayload(a: Automation, existingNames: readonly string[]) {
  return {
    name: copyName(a.name, existingNames),
    description: a.description ?? null,
    enabled: false,
    canvas: a.canvas ?? {},
    nodes: a.nodes.map((n) => ({
      node_key: n.node_key,
      kind: n.kind,
      type: n.type,
      config: n.config ?? {},
      position_x: n.position_x,
      position_y: n.position_y,
    })),
    edges: a.edges.map((e) => ({
      source_key: e.source_key,
      target_key: e.target_key,
      branch: e.branch,
    })),
  };
}

export function AutomazioniPanel() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Automation | null>(null);
  const [creating, setCreating] = useState(false);

  const automations = useQuery({
    queryKey: ['automations'],
    queryFn: async (): Promise<Automation[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/automations');
      if (error) throw new Error(apiErrorMessage(error));
      return data as Automation[];
    },
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['automations'] });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const api = getApiClient();
      const { error } = await api.DELETE('/automations/{automation_id}', {
        params: { path: { automation_id: id } },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: invalidate,
  });

  const duplicate = useMutation({
    mutationFn: async (a: Automation) => {
      const api = getApiClient();
      const existingNames = (automations.data ?? []).map((r) => r.name);
      const { error } = await api.POST('/automations', {
        body: duplicatePayload(a, existingNames) as never,
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: invalidate,
  });

  const closeEditor = () => {
    setEditing(null);
    setCreating(false);
  };

  if (creating || editing) {
    return (
      <div className="p-6">
        <AutomationEditor editing={editing} onDone={closeEditor} />
      </div>
    );
  }

  if (automations.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento automazioni…</div>;
  }
  if (automations.isError) {
    return (
      <div className="p-6 text-sm text-destructive">
        Errore:{' '}
        {automations.error instanceof Error ? automations.error.message : 'sconosciuto'}
      </div>
    );
  }

  const rows = automations.data ?? [];

  const card = (a: Automation) => {
    const triggerLabel = a.trigger_type
      ? (TRIGGER_LABEL[a.trigger_type] ?? a.trigger_type)
      : '—';
    const duplicating = duplicate.isPending && duplicate.variables?.id === a.id;
    return (
      <Card key={a.id}>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base">{a.name}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Trigger: {triggerLabel} · {a.nodes.length} nodi · {a.edges.length} collegamenti
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant={a.enabled ? 'success' : 'secondary'}>
              {a.enabled ? 'Attiva' : 'Bozza'}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditing(a)}>
              Apri sulla lavagnetta
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => duplicate.mutate(a)}
              disabled={duplicate.isPending}
              title="Crea una copia dell'automazione come bozza"
            >
              <Copy size={14} />
              {duplicating ? 'Duplicazione…' : 'Duplica'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => remove.mutate(a.id)}
              disabled={remove.isPending}
            >
              Elimina
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Configura tutte le automazioni dalla lavagnetta: scegli un trigger (es. «Messaggio
          ricevuto», «Nessuna risposta», «Prenotazione creata»), aggiungi condizioni e azioni con i
          blocchi, poi attivala.
        </p>
        <Button onClick={() => setCreating(true)}>Nuova automazione</Button>
      </div>

      {duplicate.error ? (
        <p className="text-xs text-destructive">
          Duplicazione non riuscita:{' '}
          {duplicate.error instanceof Error ? duplicate.error.message : 'errore sconosciuto'}
        </p>
      ) : null}

      <div className="space-y-3">
        {rows.length === 0 ? (
          <Card>
            <CardContent className="py-6 text-center text-sm text-muted-foreground">
              Nessuna automazione. Creane una: scegli un trigger (es. «Messaggio ricevuto»),
              aggiungi condizioni e azioni sulla lavagnetta.
            </CardContent>
          </Card>
        ) : (
          rows.map(card)
        )}
      </div>
    </div>
  );
}
