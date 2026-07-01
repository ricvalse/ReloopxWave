'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { AutomationEditor } from './automation-editor';

type Automation = components['schemas']['AutomationOut'];

const TRIGGER_LABEL: Record<string, string> = {
  message_received: 'Messaggio ricevuto',
  no_answer: 'Nessuna risposta',
  booking_created: 'Prenotazione creata',
  booking_failed: 'Prenotazione fallita',
  lead_dormant: 'Lead dormiente',
  // system_key labels
  reactivation: 'Riattivazione dormienti',
  booking_reminder: 'Promemoria appuntamento',
  first_contact: 'Primo contatto',
};

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
  const systemRows = rows.filter((a) => a.is_system);
  const customRows = rows.filter((a) => !a.is_system);

  const card = (a: Automation) => {
    const triggerLabel = a.system_key
      ? (TRIGGER_LABEL[a.system_key] ?? a.name)
      : a.trigger_type
        ? (TRIGGER_LABEL[a.trigger_type] ?? a.trigger_type)
        : '—';
    return (
      <Card key={a.id}>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle className="text-base">{a.name}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              {a.is_system ? 'Evento' : 'Trigger'}: {triggerLabel} · {a.nodes.length} nodi
              · {a.edges.length} collegamenti
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {a.is_system ? <Badge variant="outline">Sistema</Badge> : null}
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
            {!a.is_system ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => remove.mutate(a.id)}
                disabled={remove.isPending}
              >
                Elimina
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Configura tutte le automazioni dalla lavagnetta. I flussi di sistema (nessuna risposta,
          riattivazione, promemoria appuntamento) gestiscono tempi, tentativi e messaggi con i
          blocchi; attivali per usarli al posto dei valori predefiniti.
        </p>
        <Button onClick={() => setCreating(true)}>Nuova automazione</Button>
      </div>

      <div className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Flussi di sistema
        </p>
        {systemRows.length === 0 ? (
          <Card>
            <CardContent className="py-6 text-center text-sm text-muted-foreground">
              Caricamento flussi di sistema…
            </CardContent>
          </Card>
        ) : (
          systemRows.map(card)
        )}
      </div>

      <div className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Automazioni personalizzate
        </p>
        {customRows.length === 0 ? (
          <Card>
            <CardContent className="py-6 text-center text-sm text-muted-foreground">
              Nessuna automazione personalizzata. Creane una: scegli un trigger (es. «Messaggio
              ricevuto»), aggiungi condizioni e azioni sulla lavagnetta.
            </CardContent>
          </Card>
        ) : (
          customRows.map(card)
        )}
      </div>
    </div>
  );
}
