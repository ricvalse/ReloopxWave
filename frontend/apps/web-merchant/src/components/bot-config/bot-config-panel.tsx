'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { getBrowserSupabase } from '@/lib/supabase';

type BotConfig = components['schemas']['BotConfigSchema'];
type OverridesOut = components['schemas']['OverridesOut'];

type OverrideBag = Record<string, Record<string, unknown>>;
type FormState = Record<string, unknown>; // flat, dotted keys

type FieldKind = 'int' | 'float' | 'text' | 'bool' | 'textarea';

type FieldDef = {
  key: string; // dotted path, e.g. "no_answer.first_reminder_min"
  label: string;
  kind: FieldKind;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  help?: string;
  rows?: number;
};

type SectionDef = {
  section: string; // top-level key, e.g. "no_answer"
  title: string;
  description: string;
  fields: FieldDef[];
};

const SECTIONS: SectionDef[] = [
  {
    section: 'business',
    title: 'Profilo attività',
    description:
      'Il bot risponde a nome dell’attività. Più sono completi questi campi, più pertinenti saranno le risposte.',
    fields: [
      { key: 'business.name', label: 'Nome attività', kind: 'text', placeholder: 'es. Studio Dentistico Rossi' },
      { key: 'business.industry', label: 'Settore', kind: 'text', placeholder: 'es. dentistico, consulenza, e-commerce' },
      {
        key: 'business.description',
        label: 'Descrizione breve',
        kind: 'textarea',
        rows: 3,
        placeholder: 'Chi siete, cosa offrite, cosa vi distingue.',
      },
      {
        key: 'business.offer',
        label: 'Offerta principale',
        kind: 'textarea',
        rows: 3,
        placeholder: 'Prodotti / servizi principali che il bot deve proporre.',
      },
      { key: 'business.hours', label: 'Orari', kind: 'text', placeholder: 'Lun-Ven 9:00-19:00' },
      { key: 'business.location', label: 'Sede / copertura', kind: 'text', placeholder: 'es. Milano, online in tutta Italia' },
      {
        key: 'business.pricing_notes',
        label: 'Note sui prezzi',
        kind: 'textarea',
        rows: 2,
        placeholder: 'Come parlare di prezzi; es. “a partire da 50€, preventivo su misura”.',
      },
      { key: 'business.website', label: 'Sito web', kind: 'text', placeholder: 'https://…' },
    ],
  },
  {
    section: 'no_answer',
    title: 'No answer (UC-03)',
    description: 'Follow-up se il lead non risponde.',
    fields: [
      { key: 'no_answer.first_reminder_min', label: '1° reminder (min)', kind: 'int', min: 30, max: 480 },
      { key: 'no_answer.second_reminder_min', label: '2° reminder (min)', kind: 'int', min: 720, max: 2880 },
      { key: 'no_answer.max_followups', label: 'Max follow-up', kind: 'int', min: 1, max: 4 },
    ],
  },
  {
    section: 'reactivation',
    title: 'Reactivation (UC-06)',
    description: 'Riattivazione lead dormienti.',
    fields: [
      { key: 'reactivation.dormant_days', label: 'Giorni dormienza', kind: 'int', min: 30, max: 180 },
      { key: 'reactivation.interval_days', label: 'Intervallo tentativi (giorni)', kind: 'int', min: 3, max: 30 },
      { key: 'reactivation.max_attempts', label: 'Max tentativi', kind: 'int', min: 1, max: 5 },
    ],
  },
  {
    section: 'scoring',
    title: 'Scoring (UC-05)',
    description: 'Soglie per classificare hot / cold.',
    fields: [
      { key: 'scoring.hot_threshold', label: 'Hot threshold', kind: 'int', min: 50, max: 100 },
      { key: 'scoring.cold_threshold', label: 'Cold threshold', kind: 'int', min: 0, max: 50 },
    ],
  },
  {
    section: 'rag',
    title: 'RAG (UC-07)',
    description: 'Retrieval dalla knowledge base.',
    fields: [
      { key: 'rag.top_k', label: 'Top K', kind: 'int', min: 3, max: 10 },
      { key: 'rag.min_score', label: 'Soglia minima', kind: 'float', min: 0.5, max: 0.9, step: 0.05 },
    ],
  },
  {
    section: 'pipeline',
    title: 'Pipeline (UC-04)',
    description:
      'Quando il bot promuove un lead. Il pipeline + new stage servono al booking per creare l’opportunità in GHL; il qualified stage è dove il bot la sposta quando il lead si qualifica.',
    fields: [
      { key: 'pipeline.advance_threshold', label: 'Soglia avanzamento', kind: 'int', min: 0, max: 100 },
      { key: 'pipeline.default_pipeline_id', label: 'GHL pipeline ID (default)', kind: 'text' },
      { key: 'pipeline.new_stage_id', label: 'GHL new-lead stage ID', kind: 'text' },
      { key: 'pipeline.qualified_stage_id', label: 'GHL qualified stage ID', kind: 'text' },
    ],
  },
  {
    section: 'booking',
    title: 'Booking (UC-02)',
    description: 'Default calendario e durata appuntamenti.',
    fields: [
      { key: 'booking.default_duration_min', label: 'Durata default (min)', kind: 'int', min: 15, max: 240 },
      { key: 'booking.lookahead_days', label: 'Lookahead (giorni)', kind: 'int', min: 1, max: 60 },
      { key: 'booking.default_calendar_id', label: 'Calendar ID default', kind: 'text' },
    ],
  },
  {
    section: 'schedule',
    title: 'Orari',
    description: 'Orari attivi, messaggio fuori orario, timezone.',
    fields: [
      { key: 'schedule.active_hours', label: 'Orari attivi', kind: 'text' },
      { key: 'schedule.off_hours_message', label: 'Messaggio fuori orario', kind: 'text' },
      { key: 'schedule.timezone', label: 'Timezone', kind: 'text' },
    ],
  },
  {
    section: 'bot',
    title: 'Bot',
    description: 'Voce, tono e istruzioni extra per il prompt di sistema.',
    fields: [
      { key: 'bot.language', label: 'Lingua', kind: 'text', placeholder: 'it' },
      { key: 'bot.tone', label: 'Tono', kind: 'text', placeholder: 'professionale-amichevole' },
      {
        key: 'bot.system_prompt_additions',
        label: 'Istruzioni aggiuntive',
        kind: 'textarea',
        rows: 4,
        placeholder:
          'Regole aggiuntive che vuoi dare al bot (stile, argomenti da evitare, script particolari).',
      },
      {
        key: 'bot.first_message',
        label: 'Messaggio di benvenuto',
        kind: 'textarea',
        rows: 2,
        placeholder: 'Primo messaggio quando scriviamo a un nuovo lead.',
      },
    ],
  },
  {
    section: 'escalation',
    title: 'Escalation',
    description: 'Abilita routing a gpt-5.2 per casi complessi.',
    fields: [
      { key: 'escalation.enabled', label: 'Abilitata', kind: 'bool' },
    ],
  },
  {
    section: 'privacy',
    title: 'Privacy',
    description: 'Retention dati conversazioni.',
    fields: [
      { key: 'privacy.retention_months', label: 'Retention (mesi)', kind: 'int', min: 6, max: 60 },
    ],
  },
  {
    section: 'ab_test',
    title: 'A/B testing',
    description: 'Defaults sperimentazione.',
    fields: [
      { key: 'ab_test.min_sample', label: 'Min sample size', kind: 'int', min: 50, max: 1000 },
    ],
  },
];

async function loadMerchantId(): Promise<string | null> {
  const supabase = getBrowserSupabase();
  const { data } = await supabase.auth.getSession();
  const claims = (data.session?.user?.app_metadata as Record<string, unknown> | undefined) ?? {};
  return typeof claims.merchant_id === 'string' ? claims.merchant_id : null;
}

export function BotConfigPanel() {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);

  const merchantQuery = useQuery({
    queryKey: ['auth', 'merchant-id'],
    queryFn: loadMerchantId,
    staleTime: 5 * 60_000,
  });
  const merchantId = merchantQuery.data ?? null;

  const resolvedQuery = useQuery({
    queryKey: ['bot-config', 'resolved', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<BotConfig> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/resolved' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as BotConfig;
    },
  });

  const overridesQuery = useQuery({
    queryKey: ['bot-config', 'overrides', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<OverridesOut> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/overrides' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as OverridesOut;
    },
  });

  const resolvedFlat = useMemo<FormState>(
    () => (resolvedQuery.data ? flatten(resolvedQuery.data as Record<string, unknown>) : {}),
    [resolvedQuery.data],
  );
  const overridesFlat = useMemo<FormState>(
    () => (overridesQuery.data ? flatten(overridesQuery.data.overrides) : {}),
    [overridesQuery.data],
  );
  const lockedSet = useMemo(
    () => new Set(overridesQuery.data?.locked_keys ?? []),
    [overridesQuery.data],
  );

  // Form state holds current input values (flat dotted keys). Starts as
  // resolved values; user edits flow into it. Separate dirty set marks keys
  // the user has touched, so we save only those.
  const [form, setForm] = useState<FormState>({});
  const [dirty, setDirty] = useState<Set<string>>(new Set());

  useEffect(() => {
    setForm(resolvedFlat);
    setDirty(new Set());
  }, [resolvedFlat]);

  const save = useMutation({
    mutationFn: async () => {
      if (!merchantId) throw new Error('Merchant context mancante');
      // Keep existing overrides for keys the user didn't touch, layer the
      // dirty keys on top. Locked keys get stripped server-side.
      const bag: FormState = { ...overridesFlat };
      for (const key of dirty) {
        bag[key] = form[key];
      }
      const nested = inflate(bag);
      const api = getApiClient();
      const { data, error } = await api.PUT(
        '/bot-config/{merchant_id}/overrides' as never,
        {
          params: { path: { merchant_id: merchantId } },
          body: { overrides: nested },
        } as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bot-config', 'resolved', merchantId] });
      void queryClient.invalidateQueries({ queryKey: ['bot-config', 'overrides', merchantId] });
      setDirty(new Set());
      setFormError(null);
    },
    onError: (err) => setFormError(err instanceof Error ? err.message : 'Errore salvataggio'),
  });

  const resetField = (key: string) => {
    setForm((prev) => ({ ...prev, [key]: resolvedFlat[key] }));
    setDirty((prev) => {
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
  };

  const resetAll = () => {
    setForm(resolvedFlat);
    setDirty(new Set());
  };

  if (merchantQuery.isLoading || resolvedQuery.isLoading || overridesQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento configurazione…</div>;
  }
  if (!merchantId) {
    return (
      <div className="p-6 text-sm text-destructive">
        Merchant context mancante nel JWT. Ricarica dopo il login o contatta l&apos;agenzia.
      </div>
    );
  }
  if (resolvedQuery.isError) {
    return (
      <div className="p-6 text-sm text-destructive">
        {resolvedQuery.error instanceof Error ? resolvedQuery.error.message : 'Errore'}
      </div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      <div className="rounded-md border bg-muted/30 px-4 py-3 text-sm">
        <p className="font-medium">Come funziona</p>
        <p className="mt-1 text-muted-foreground">
          Ogni campo mostra il valore risolto dalla cascata merchant → agenzia → sistema
          (§9). Modifica solo i campi che vuoi personalizzare; i campi bloccati
          dall&apos;agenzia non sono modificabili.
        </p>
      </div>

      {SECTIONS.map((s) => (
        <SectionCard
          key={s.section}
          section={s}
          form={form}
          overridesFlat={overridesFlat}
          resolvedFlat={resolvedFlat}
          lockedSet={lockedSet}
          dirty={dirty}
          onChange={(key, value) => {
            setForm((prev) => ({ ...prev, [key]: value }));
            setDirty((prev) => new Set(prev).add(key));
          }}
          onReset={resetField}
        />
      ))}

      {formError ? (
        <p className="text-sm text-destructive">{formError}</p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={resetAll} disabled={dirty.size === 0 || save.isPending}>
          Scarta modifiche
        </Button>
        <Button onClick={() => save.mutate()} disabled={dirty.size === 0 || save.isPending}>
          {save.isPending ? 'Salvataggio…' : `Salva (${dirty.size})`}
        </Button>
      </div>
    </div>
  );
}

function SectionCard({
  section,
  form,
  overridesFlat,
  resolvedFlat,
  lockedSet,
  dirty,
  onChange,
  onReset,
}: {
  section: SectionDef;
  form: FormState;
  overridesFlat: FormState;
  resolvedFlat: FormState;
  lockedSet: Set<string>;
  dirty: Set<string>;
  onChange: (key: string, value: unknown) => void;
  onReset: (key: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{section.title}</CardTitle>
        <p className="text-sm text-muted-foreground">{section.description}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {section.fields.map((f) => {
          const locked = lockedSet.has(f.key);
          const hasOverride = Object.prototype.hasOwnProperty.call(overridesFlat, f.key);
          const isDirty = dirty.has(f.key);
          const badge = locked
            ? ('locked' as const)
            : hasOverride || isDirty
              ? ('customized' as const)
              : ('inherited' as const);
          return (
            <FieldRow
              key={f.key}
              field={f}
              value={form[f.key]}
              inheritedValue={resolvedFlat[f.key]}
              badge={badge}
              locked={locked}
              isDirty={isDirty}
              onChange={(v) => onChange(f.key, v)}
              onReset={() => onReset(f.key)}
            />
          );
        })}
      </CardContent>
    </Card>
  );
}

function FieldRow({
  field,
  value,
  inheritedValue,
  badge,
  locked,
  isDirty,
  onChange,
  onReset,
}: {
  field: FieldDef;
  value: unknown;
  inheritedValue: unknown;
  badge: 'inherited' | 'customized' | 'locked';
  locked: boolean;
  isDirty: boolean;
  onChange: (v: unknown) => void;
  onReset: () => void;
}) {
  return (
    <div className="grid items-center gap-2 md:grid-cols-[1fr_auto] md:gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={field.key} className="text-sm font-medium">
          {field.label}
        </label>
        <Badge kind={badge} />
        {isDirty ? (
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={onReset}
          >
            Reset
          </button>
        ) : null}
      </div>
      <FieldInput
        field={field}
        value={value}
        disabled={locked}
        onChange={onChange}
        placeholder={
          field.kind === 'text' && inheritedValue !== null && inheritedValue !== undefined
            ? String(inheritedValue)
            : undefined
        }
      />
    </div>
  );
}

function FieldInput({
  field,
  value,
  disabled,
  onChange,
  placeholder,
}: {
  field: FieldDef;
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
  placeholder?: string;
}) {
  if (field.kind === 'bool') {
    return (
      <input
        id={field.key}
        type="checkbox"
        disabled={disabled}
        checked={!!value}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4"
      />
    );
  }
  if (field.kind === 'int' || field.kind === 'float') {
    return (
      <input
        id={field.key}
        type="number"
        disabled={disabled}
        min={field.min}
        max={field.max}
        step={field.step ?? (field.kind === 'int' ? 1 : 0.01)}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === '') {
            onChange(null);
            return;
          }
          const n = field.kind === 'int' ? parseInt(raw, 10) : parseFloat(raw);
          onChange(Number.isNaN(n) ? null : n);
        }}
        className="h-9 w-32 rounded-md border border-input bg-background px-3 text-sm"
      />
    );
  }
  if (field.kind === 'textarea') {
    return (
      <textarea
        id={field.key}
        disabled={disabled}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(e) => onChange(e.target.value || null)}
        placeholder={placeholder ?? field.placeholder}
        rows={field.rows ?? 3}
        className="w-full min-w-[18rem] max-w-xl rounded-md border border-input bg-background px-3 py-2 text-sm md:w-[32rem]"
      />
    );
  }
  return (
    <input
      id={field.key}
      type="text"
      disabled={disabled}
      value={value === null || value === undefined ? '' : String(value)}
      onChange={(e) => onChange(e.target.value || null)}
      placeholder={placeholder ?? field.placeholder}
      className="h-9 w-72 rounded-md border border-input bg-background px-3 text-sm"
    />
  );
}

function Badge({ kind }: { kind: 'inherited' | 'customized' | 'locked' }) {
  if (kind === 'locked') {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 ring-1 ring-inset ring-amber-200">
        🔒 Locked
      </span>
    );
  }
  if (kind === 'customized') {
    return (
      <span className="inline-flex items-center rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-900 ring-1 ring-inset ring-blue-200">
        Customized
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground ring-1 ring-inset ring-border">
      Inherited
    </span>
  );
}

// ---- helpers --------------------------------------------------------------

function flatten(
  obj: Record<string, unknown>,
  prefix = '',
  out: FormState = {},
): FormState {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (
      value !== null &&
      typeof value === 'object' &&
      !Array.isArray(value)
    ) {
      flatten(value as Record<string, unknown>, path, out);
    } else {
      out[path] = value;
    }
  }
  return out;
}

function inflate(flat: FormState): OverrideBag {
  const out: OverrideBag = {};
  for (const [path, value] of Object.entries(flat)) {
    if (value === null || value === undefined) continue;
    const parts = path.split('.');
    if (parts.length === 0) continue;
    const leaf = parts[parts.length - 1] as string;
    let node: Record<string, unknown> = out as unknown as Record<string, unknown>;
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i] as string;
      if (!Object.prototype.hasOwnProperty.call(node, seg) || typeof node[seg] !== 'object') {
        node[seg] = {};
      }
      node = node[seg] as Record<string, unknown>;
    }
    node[leaf] = value;
  }
  return out;
}
