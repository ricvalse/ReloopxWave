'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Switch,
} from '@reloop/ui';
import { apiErrorMessage, getApiClient } from '@/lib/api';

type Flow = components['schemas']['FlowOut'];
type FlowStepOut = components['schemas']['FlowStepOut'];
type Template = components['schemas']['WhatsAppTemplateOut'];

type StepDraft = {
  step_index: number;
  delay_minutes: number;
  template_id: string | null;
  window_policy: string;
  free_text: string;
  enabled: boolean;
};

const LIFECYCLE = [
  { key: 'no_answer', label: 'No-answer (nessuna risposta)' },
  { key: 'reactivation', label: 'Riattivazione dormienti' },
  { key: 'booking_reminder', label: 'Promemoria appuntamento' },
  { key: 'first_contact', label: 'Primo contatto' },
];

const WINDOW_POLICIES = [
  { value: 'auto', label: 'Auto (testo se in finestra, altrimenti template)' },
  { value: 'require_template', label: 'Sempre template' },
  { value: 'freeform_only', label: 'Solo testo (salta fuori finestra)' },
];

export function FlowsPanel() {
  const flows = useQuery({
    queryKey: ['flows'],
    queryFn: async (): Promise<Flow[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/flows');
      if (error) throw new Error(apiErrorMessage(error));
      return data as Flow[];
    },
  });

  const templates = useQuery({
    queryKey: ['whatsapp-templates'],
    queryFn: async (): Promise<Template[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/whatsapp-templates');
      if (error) throw new Error(apiErrorMessage(error));
      return data as Template[];
    },
  });

  if (flows.isLoading || templates.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento flussi…</div>;
  }
  if (flows.isError || templates.isError) {
    const err = flows.error ?? templates.error;
    return (
      <div className="p-6 text-sm text-destructive">
        Errore: {err instanceof Error ? err.message : 'sconosciuto'}
      </div>
    );
  }

  const byKey = new Map((flows.data ?? []).map((f) => [f.key, f]));
  // V1: the step editor can't supply per-variable mappings yet, so only offer
  // approved templates WITHOUT body variables — those send correctly as-is.
  const approved = (templates.data ?? []).filter(
    (t) => t.status === 'approved' && (t.variables?.length ?? 0) === 0,
  );

  return (
    <div className="space-y-4 p-6">
      {approved.length === 0 ? (
        <div className="rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm">
          Nessun template approvato: i passi fuori dalla finestra di 24h verranno saltati finché non
          approvi un template.
        </div>
      ) : null}
      {LIFECYCLE.map((lc) => (
        <FlowEditor
          key={lc.key}
          flowKey={lc.key}
          label={lc.label}
          flow={byKey.get(lc.key)}
          approvedTemplates={approved}
        />
      ))}
    </div>
  );
}

function toDraft(steps: FlowStepOut[]): StepDraft[] {
  return steps
    .slice()
    .sort((a, b) => a.step_index - b.step_index)
    .map((s) => ({
      step_index: s.step_index,
      delay_minutes: s.delay_minutes,
      template_id: s.template_id ?? null,
      window_policy: s.window_policy,
      free_text: s.free_text ?? '',
      enabled: s.enabled,
    }));
}

function FlowEditor({
  flowKey,
  label,
  flow,
  approvedTemplates,
}: {
  flowKey: string;
  label: string;
  flow: Flow | undefined;
  approvedTemplates: Template[];
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(flow?.name ?? label);
  const [enabled, setEnabled] = useState(flow?.enabled ?? true);
  const [steps, setSteps] = useState<StepDraft[]>(flow ? toDraft(flow.steps) : []);

  const save = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { error } = await api.PUT('/flows/{key}', {
        params: { path: { key: flowKey } },
        body: {
          name,
          enabled,
          steps: steps.map((s, i) => ({
            step_index: i,
            delay_minutes: s.delay_minutes,
            template_id: s.template_id,
            variable_mapping: {},
            window_policy: s.window_policy,
            free_text: s.free_text || null,
            enabled: s.enabled,
          })),
        },
      });
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['flows'] }),
  });

  const updateStep = (i: number, patch: Partial<StepDraft>) =>
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));

  const addStep = () =>
    setSteps((prev) => [
      ...prev,
      {
        step_index: prev.length,
        delay_minutes: 0,
        template_id: null,
        window_policy: 'auto',
        free_text: '',
        enabled: true,
      },
    ]);

  const removeStep = (i: number) => setSteps((prev) => prev.filter((_, idx) => idx !== i));

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Switch checked={enabled} onCheckedChange={setEnabled} />
          <CardTitle className="text-base">{label}</CardTitle>
        </div>
        <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? 'Salvataggio…' : 'Salva'}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1">
          <Label htmlFor={`name-${flowKey}`}>Nome flusso</Label>
          <Input
            id={`name-${flowKey}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        {steps.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nessun passo configurato — usa la copia predefinita del sistema.
          </p>
        ) : (
          steps.map((s, i) => (
            <div key={i} className="space-y-3 rounded-md border border-border p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">Passo {i + 1}</span>
                <Button variant="ghost" size="sm" onClick={() => removeStep(i)}>
                  Rimuovi
                </Button>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div className="space-y-1">
                  <Label>Ritardo (min)</Label>
                  <Input
                    type="number"
                    min={0}
                    value={s.delay_minutes}
                    onChange={(e) =>
                      updateStep(i, { delay_minutes: Number(e.target.value) || 0 })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label>Template</Label>
                  <select
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                    value={s.template_id ?? ''}
                    onChange={(e) => updateStep(i, { template_id: e.target.value || null })}
                  >
                    <option value="">— nessuno (solo testo) —</option>
                    {approvedTemplates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label>Finestra 24h</Label>
                  <select
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                    value={s.window_policy}
                    onChange={(e) => updateStep(i, { window_policy: e.target.value })}
                  >
                    {WINDOW_POLICIES.map((w) => (
                      <option key={w.value} value={w.value}>
                        {w.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="space-y-1">
                <Label>Testo libero (usato dentro la finestra 24h)</Label>
                <Input
                  value={s.free_text}
                  onChange={(e) => updateStep(i, { free_text: e.target.value })}
                />
              </div>
            </div>
          ))
        )}

        <div className="flex items-center justify-between">
          <Button variant="outline" size="sm" onClick={addStep}>
            + Aggiungi passo
          </Button>
          {save.error ? (
            <span className="text-sm text-destructive">
              {save.error instanceof Error ? save.error.message : 'Errore'}
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
