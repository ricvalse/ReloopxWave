'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { BulkApplyDialog } from '@/components/merchants/bulk-apply-dialog';

type Template = components['schemas']['TemplateOut'];
type TemplateIn = components['schemas']['TemplateIn'];

// ---- Per-field template editor (UC-10) -----------------------------------
// Replaces the raw-JSON textareas: the agency sets each default value via a
// typed control and toggles a per-key lock (locked_keys). Keys map 1:1 to
// `BotConfigSchema` dotted paths; the backend still validates on write.

type TKind = 'text' | 'int' | 'float' | 'bool' | 'select';
type TField = {
  key: string; // dotted path, e.g. "scoring.hot_threshold"
  label: string;
  kind: TKind;
  min?: number;
  max?: number;
  step?: number;
  options?: { value: string; label: string }[];
  placeholder?: string;
};
type TSection = { title: string; fields: TField[] };

const TEMPLATE_SECTIONS: TSection[] = [
  {
    title: 'Scoring (UC-05)',
    fields: [
      { key: 'scoring.hot_threshold', label: 'Hot threshold', kind: 'int', min: 50, max: 100 },
      { key: 'scoring.cold_threshold', label: 'Cold threshold', kind: 'int', min: 0, max: 50 },
    ],
  },
  {
    title: 'No answer (UC-03)',
    fields: [
      { key: 'no_answer.first_reminder_min', label: '1° reminder (min)', kind: 'int', min: 30, max: 480 },
      { key: 'no_answer.second_reminder_min', label: '2° reminder (min)', kind: 'int', min: 720, max: 2880 },
      { key: 'no_answer.max_followups', label: 'Max follow-up', kind: 'int', min: 1, max: 4 },
    ],
  },
  {
    title: 'Riattivazione (UC-06)',
    fields: [
      { key: 'reactivation.dormant_days', label: 'Giorni dormienza', kind: 'int', min: 30, max: 180 },
      { key: 'reactivation.interval_days', label: 'Intervallo (giorni)', kind: 'int', min: 3, max: 30 },
      { key: 'reactivation.max_attempts', label: 'Max tentativi', kind: 'int', min: 1, max: 5 },
    ],
  },
  {
    title: 'Pipeline (UC-04)',
    fields: [
      { key: 'pipeline.advance_threshold', label: 'Soglia avanzamento', kind: 'int', min: 0, max: 100 },
      { key: 'pipeline.default_pipeline_id', label: 'GHL pipeline ID', kind: 'text' },
      { key: 'pipeline.new_stage_id', label: 'GHL new-lead stage ID', kind: 'text' },
      { key: 'pipeline.qualified_stage_id', label: 'GHL qualified stage ID', kind: 'text' },
    ],
  },
  {
    title: 'Booking (UC-02)',
    fields: [
      { key: 'booking.default_duration_min', label: 'Durata default (min)', kind: 'int', min: 15, max: 240 },
      { key: 'booking.lookahead_days', label: 'Lookahead (giorni)', kind: 'int', min: 1, max: 60 },
    ],
  },
  {
    title: 'RAG (UC-07)',
    fields: [
      { key: 'rag.top_k', label: 'Top K', kind: 'int', min: 3, max: 10 },
      { key: 'rag.min_score', label: 'Soglia minima', kind: 'float', min: 0.5, max: 0.9, step: 0.05 },
    ],
  },
  {
    title: 'Orari',
    fields: [
      { key: 'schedule.active_hours', label: 'Orari attivi', kind: 'text', placeholder: '24/7 oppure 09:00-18:00' },
      { key: 'schedule.timezone', label: 'Timezone', kind: 'text', placeholder: 'Europe/Rome' },
      { key: 'schedule.off_hours_message', label: 'Messaggio fuori orario', kind: 'text' },
    ],
  },
  {
    title: 'Bot — Persona',
    fields: [
      { key: 'bot.language', label: 'Lingua', kind: 'text', placeholder: 'it' },
      {
        key: 'bot.formality',
        label: 'Tono',
        kind: 'select',
        options: [
          { value: 'auto', label: 'Automatico' },
          { value: 'dai-del-tu', label: 'Dai del tu' },
          { value: 'dai-del-lei', label: 'Dai del Lei' },
        ],
      },
      {
        key: 'bot.verbosity',
        label: 'Lunghezza',
        kind: 'select',
        options: [
          { value: 'conciso', label: 'Conciso' },
          { value: 'equilibrato', label: 'Equilibrato' },
          { value: 'dettagliato', label: 'Dettagliato' },
        ],
      },
      {
        key: 'bot.emoji_policy',
        label: 'Emoji',
        kind: 'select',
        options: [
          { value: 'mai', label: 'Mai' },
          { value: 'sobrio', label: 'Sobrio' },
          { value: 'libero', label: 'Libero' },
        ],
      },
      { key: 'bot.auto_reply_enabled', label: 'Risposta automatica', kind: 'bool' },
    ],
  },
  {
    title: 'Escalation / Privacy',
    fields: [
      { key: 'escalation.enabled', label: 'Escalation attiva', kind: 'bool' },
      { key: 'privacy.retention_months', label: 'Retention (mesi)', kind: 'int', min: 6, max: 60 },
    ],
  },
];

const EMPTY_DRAFT: Draft = {
  id: null,
  name: '',
  description: '',
  values: {},
  locked: [],
  isDefault: false,
};

type Draft = {
  id: string | null;
  name: string;
  description: string;
  values: Record<string, unknown>; // dotted key -> value (unset keys omitted on save)
  locked: string[]; // dotted keys
  isDefault: boolean;
};

export function TemplatesPanel() {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [bulkTemplate, setBulkTemplate] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const list = useQuery({
    queryKey: ['templates', 'list'],
    queryFn: async (): Promise<Template[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/templates');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Template[];
    },
  });

  return (
    <>
      <PageHeader
        title="Template bot"
        description="UC-10 — default e parametri che ogni merchant eredita per il suo bot."
        actions={
          <Button onClick={() => setDraft(draft ? null : { ...EMPTY_DRAFT })}>
            {draft && draft.id === null ? 'Annulla' : '+ Nuovo template'}
          </Button>
        }
      />
      {draft ? (
        <TemplateEditor
          key={draft.id ?? 'new'}
          draft={draft}
          onClose={() => setDraft(null)}
          onSaved={() => {
            setDraft(null);
            void queryClient.invalidateQueries({ queryKey: ['templates', 'list'] });
          }}
        />
      ) : null}
      <TemplateList
        query={list}
        onEdit={(t) =>
          setDraft({
            id: t.id,
            name: t.name,
            description: t.description ?? '',
            values: flatten(t.defaults as Record<string, unknown>),
            locked: [...t.locked_keys],
            isDefault: t.is_default,
          })
        }
        onApplyToMerchants={(t) => setBulkTemplate(t.id)}
      />
      <BulkApplyDialog
        open={bulkTemplate !== null}
        onClose={() => setBulkTemplate(null)}
        preselectedTemplateId={bulkTemplate ?? undefined}
      />
    </>
  );
}

function TemplateList({
  query,
  onEdit,
  onApplyToMerchants,
}: {
  query: ReturnType<typeof useQuery<Template[]>>;
  onEdit: (t: Template) => void;
  onApplyToMerchants: (t: Template) => void;
}) {
  if (query.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento template…</div>;
  }
  if (query.isError) {
    return (
      <div className="p-6 text-sm text-destructive">
        {query.error instanceof Error ? query.error.message : 'Errore sconosciuto'}
      </div>
    );
  }
  const templates = query.data ?? [];
  if (templates.length === 0) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Nessun template creato. Usa <strong>+ Nuovo template</strong> per definire i default
            dell&apos;agenzia.
          </CardContent>
        </Card>
      </div>
    );
  }
  return (
    <div className="space-y-3 p-6">
      {templates.map((t) => (
        <Card key={t.id}>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2">
                {t.name}
                {t.is_default ? (
                  <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary ring-1 ring-primary/20">
                    Default
                  </span>
                ) : null}
              </CardTitle>
              {t.description ? (
                <p className="mt-1 text-sm text-muted-foreground">{t.description}</p>
              ) : null}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => onApplyToMerchants(t)}>
                Applica a merchant…
              </Button>
              <Button variant="outline" size="sm" onClick={() => onEdit(t)}>
                Modifica
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm md:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Chiavi bloccate</dt>
                <dd>{t.locked_keys.length}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Default impostati</dt>
                <dd>{Object.keys(flatten(t.defaults as Record<string, unknown>)).length}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">ID</dt>
                <dd className="font-mono text-xs">{t.id.slice(0, 8)}…</dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function TemplateEditor({
  draft,
  onClose,
  onSaved,
}: {
  draft: Draft;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [state, setState] = useState<Draft>(draft);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    setState(draft);
  }, [draft]);

  const lockedSet = useMemo(() => new Set(state.locked), [state.locked]);

  function setValue(key: string, value: unknown) {
    setState((s) => ({ ...s, values: { ...s.values, [key]: value } }));
  }
  function toggleLock(key: string) {
    setState((s) => ({
      ...s,
      locked: s.locked.includes(key) ? s.locked.filter((k) => k !== key) : [...s.locked, key],
    }));
  }

  const save = useMutation({
    mutationFn: async (): Promise<Template> => {
      const body: TemplateIn = {
        name: state.name,
        description: state.description || null,
        defaults: unflatten(state.values),
        locked_keys: state.locked,
        is_default: state.isDefault,
      };
      const api = getApiClient();
      if (state.id === null) {
        const { data, error } = await api.POST('/bot-config/templates', { body });
        if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
        return data as Template;
      }
      const { data, error } = await api.PUT('/bot-config/templates/{template_id}', {
        params: { path: { template_id: state.id } },
        body,
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Template;
    },
    onSuccess: onSaved,
    onError: (err) => setLocalError(err instanceof Error ? err.message : 'Errore salvataggio'),
  });

  const isNew = state.id === null;

  return (
    <div className="p-6">
      <Card>
        <CardHeader>
          <CardTitle>{isNew ? 'Nuovo template' : `Modifica: ${draft.name}`}</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="space-y-6"
            onSubmit={(e) => {
              e.preventDefault();
              setLocalError(null);
              save.mutate();
            }}
          >
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="tmpl-name">
                  Nome
                </label>
                <input
                  id="tmpl-name"
                  required
                  value={state.name}
                  onChange={(e) => setState({ ...state, name: e.target.value })}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
              </div>
              <div className="flex items-center gap-2 self-end pb-2">
                <input
                  id="tmpl-is-default"
                  type="checkbox"
                  checked={state.isDefault}
                  onChange={(e) => setState({ ...state, isDefault: e.target.checked })}
                />
                <label htmlFor="tmpl-is-default" className="text-sm">
                  Default del tenant (applicato ai nuovi merchant)
                </label>
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-sm font-medium" htmlFor="tmpl-desc">
                Descrizione
              </label>
              <input
                id="tmpl-desc"
                value={state.description}
                onChange={(e) => setState({ ...state, description: e.target.value })}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
            </div>

            <p className="text-xs text-muted-foreground">
              Lascia un campo vuoto per ereditare il default di sistema. Spunta <strong>Blocca</strong>{' '}
              per impedire al merchant di modificare quel parametro.
            </p>

            {TEMPLATE_SECTIONS.map((section) => (
              <div key={section.title} className="space-y-2">
                <h3 className="text-sm font-semibold">{section.title}</h3>
                <div className="space-y-2">
                  {section.fields.map((f) => (
                    <div
                      key={f.key}
                      className="grid items-center gap-3 md:grid-cols-[1fr_16rem_auto]"
                    >
                      <label htmlFor={`f-${f.key}`} className="text-sm">
                        {f.label}
                        <span className="ml-2 font-mono text-[10px] text-muted-foreground">
                          {f.key}
                        </span>
                      </label>
                      <TemplateFieldInput
                        field={f}
                        value={state.values[f.key]}
                        onChange={(v) => setValue(f.key, v)}
                      />
                      <label className="flex items-center gap-1 text-xs text-muted-foreground">
                        <input
                          type="checkbox"
                          checked={lockedSet.has(f.key)}
                          onChange={() => toggleLock(f.key)}
                        />
                        Blocca
                      </label>
                    </div>
                  ))}
                </div>
              </div>
            ))}

            {localError ? <p className="text-sm text-destructive">{localError}</p> : null}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={onClose} disabled={save.isPending}>
                Annulla
              </Button>
              <Button type="submit" disabled={save.isPending || !state.name}>
                {save.isPending ? 'Salvataggio…' : isNew ? 'Crea' : 'Salva'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function TemplateFieldInput({
  field,
  value,
  onChange,
}: {
  field: TField;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const cls = 'h-9 w-full rounded-md border border-input bg-background px-3 text-sm';
  if (field.kind === 'bool') {
    // Tri-state: '' = inherit (omitted), Sì = true, No = false.
    const sel = value === true ? 'true' : value === false ? 'false' : '';
    return (
      <select
        id={`f-${field.key}`}
        value={sel}
        onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value === 'true')}
        className={cls}
      >
        <option value="">Eredita</option>
        <option value="true">Sì</option>
        <option value="false">No</option>
      </select>
    );
  }
  if (field.kind === 'select') {
    return (
      <select
        id={`f-${field.key}`}
        value={value === undefined || value === null ? '' : String(value)}
        onChange={(e) => onChange(e.target.value || undefined)}
        className={cls}
      >
        <option value="">Eredita</option>
        {field.options?.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (field.kind === 'int' || field.kind === 'float') {
    return (
      <input
        id={`f-${field.key}`}
        type="number"
        min={field.min}
        max={field.max}
        step={field.step ?? (field.kind === 'int' ? 1 : 0.01)}
        value={value === undefined || value === null ? '' : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === '') return onChange(undefined);
          const n = field.kind === 'int' ? parseInt(raw, 10) : parseFloat(raw);
          onChange(Number.isNaN(n) ? undefined : n);
        }}
        className={cls}
        placeholder="Eredita"
      />
    );
  }
  return (
    <input
      id={`f-${field.key}`}
      type="text"
      value={value === undefined || value === null ? '' : String(value)}
      onChange={(e) => onChange(e.target.value || undefined)}
      className={cls}
      placeholder={field.placeholder ?? 'Eredita'}
    />
  );
}

// ---- nested <-> dotted helpers -------------------------------------------

function flatten(obj: Record<string, unknown> | null | undefined, prefix = ''): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj ?? {})) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      Object.assign(out, flatten(v as Record<string, unknown>, key));
    } else {
      out[key] = v;
    }
  }
  return out;
}

function unflatten(flat: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [dotted, v] of Object.entries(flat)) {
    if (v === '' || v === undefined || v === null) continue; // unset → inherit
    const parts = dotted.split('.');
    let node = out;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i]!;
      if (typeof node[p] !== 'object' || node[p] === null) node[p] = {};
      node = node[p] as Record<string, unknown>;
    }
    node[parts[parts.length - 1]!] = v;
  }
  return out;
}
