'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Button,
  ButtonSpinner,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  SkeletonCard,
  Switch,
} from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type TonePreset = components['schemas']['TonePreset'];
type SuggestedRules = components['schemas']['SuggestedRules'];

type BotConfig = components['schemas']['BotConfigSchema'];
type OverridesOut = components['schemas']['OverridesOut'];

type OverrideBag = Record<string, Record<string, unknown>>;
type FormState = Record<string, unknown>; // flat, dotted keys

type FieldKind = 'int' | 'float' | 'text' | 'bool' | 'textarea' | 'select' | 'tags' | 'calendar';

type BadgeKind = 'inherited' | 'customized' | 'locked' | 'lock-override';

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
  options?: { value: string; label: string }[]; // for kind: 'select'
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
      {
        key: 'booking.default_calendar_id',
        label: 'Calendario default',
        kind: 'calendar',
        help: 'Calendario GHL su cui il bot prenota. Se GHL non è collegato, inserisci l’ID manualmente.',
      },
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
    title: 'Bot — Persona',
    description:
      'Come parla il bot: registro, lunghezza, emoji, saluti e frasi tipiche. Questi controlli guidati compongono il prompt di sistema.',
    fields: [
      {
        key: 'bot.auto_reply_enabled',
        label: 'Risposta automatica',
        kind: 'bool',
        help:
          'Quando attivo, il bot risponde automaticamente ai messaggi in arrivo. Disattivandolo metti in pausa il bot per tutti i contatti — i messaggi resteranno in attesa di una tua risposta dal pannello Conversazioni.',
      },
      { key: 'bot.language', label: 'Lingua', kind: 'text', placeholder: 'it' },
      {
        key: 'bot.formality',
        label: 'Come rivolgersi al cliente',
        kind: 'select',
        options: [
          { value: 'auto', label: 'Automatico (usa il tono)' },
          { value: 'dai-del-tu', label: 'Dai del tu' },
          { value: 'dai-del-lei', label: 'Dai del Lei' },
        ],
        help: 'Su “Automatico” usa il campo Tono (sezione Avanzate).',
      },
      {
        key: 'bot.verbosity',
        label: 'Lunghezza risposte',
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
      {
        key: 'bot.greeting_style',
        label: 'Stile di apertura',
        kind: 'text',
        placeholder: 'es. saluta col nome se disponibile',
      },
      {
        key: 'bot.signature',
        label: 'Firma',
        kind: 'text',
        placeholder: 'es. — Il team di Studio Rossi',
      },
      {
        key: 'bot.do_phrases',
        label: 'Espressioni da preferire',
        kind: 'tags',
        rows: 3,
        help: 'Una per riga.',
      },
      {
        key: 'bot.dont_phrases',
        label: 'Espressioni / toni da evitare',
        kind: 'tags',
        rows: 3,
        help: 'Una per riga.',
      },
      {
        key: 'bot.sentiment_adaptation_enabled',
        label: 'Adatta il tono al sentiment',
        kind: 'bool',
        help:
          'Se il cliente sembrava insoddisfatto nel messaggio precedente, il bot apre con empatia; se ben disposto, propone il passo successivo.',
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
    section: 'bot_advanced',
    title: 'Bot — Avanzate',
    description:
      'Controlli liberi per casi particolari. Il “Tono” è usato solo quando “Come rivolgersi al cliente” è su Automatico; le istruzioni aggiuntive hanno priorità sul resto.',
    fields: [
      { key: 'bot.tone', label: 'Tono (libero)', kind: 'text', placeholder: 'professionale-amichevole' },
      {
        key: 'bot.system_prompt_additions',
        label: 'Istruzioni aggiuntive',
        kind: 'textarea',
        rows: 4,
        placeholder:
          'Regole aggiuntive che vuoi dare al bot (stile, argomenti da evitare, script particolari).',
      },
    ],
  },
  {
    section: 'delivery',
    title: 'Consegna (tono umano)',
    description:
      'Fa sembrare le risposte più umane su WhatsApp. Attivo di default (debounce, indicatore “sta scrivendo…”, breve pausa, più bolle): regola o azzera ciò che vuoi (0/disattivo = invio immediato in un solo messaggio). La finestra raggruppa messaggi ravvicinati in un’unica risposta.',
    fields: [
      {
        key: 'delivery.debounce_window_s',
        label: 'Finestra di attesa (s)',
        kind: 'int',
        min: 0,
        max: 30,
        help: '0 = risposta immediata. Es. 5 = aspetta 5s di silenzio prima di rispondere, unendo i messaggi.',
      },
      {
        key: 'delivery.typing_indicator_enabled',
        label: 'Mostra “sta scrivendo…”',
        kind: 'bool',
      },
      {
        key: 'delivery.typing_delay_max_s',
        label: 'Ritardo max “digitazione” (s)',
        kind: 'float',
        min: 0,
        max: 20,
        step: 0.5,
        help: '0 = nessun ritardo. Tetto al tempo di “digitazione” simulato.',
      },
      {
        key: 'delivery.typing_delay_base_s',
        label: 'Ritardo base (s)',
        kind: 'float',
        min: 0,
        max: 10,
        step: 0.1,
      },
      {
        key: 'delivery.typing_delay_per_char_s',
        label: 'Ritardo per carattere (s)',
        kind: 'float',
        min: 0,
        max: 0.2,
        step: 0.01,
      },
      {
        key: 'delivery.typing_delay_min_s',
        label: 'Ritardo minimo (s)',
        kind: 'float',
        min: 0,
        max: 20,
        step: 0.5,
      },
      {
        key: 'delivery.typing_jitter_frac',
        label: 'Variabilità (0–1)',
        kind: 'float',
        min: 0,
        max: 1,
        step: 0.05,
      },
      {
        key: 'delivery.multi_bubble_max',
        label: 'Max bolle per risposta',
        kind: 'int',
        min: 1,
        max: 4,
        help: '1 = una sola bolla. >1 spezza le risposte lunghe come farebbe una persona.',
      },
      {
        key: 'delivery.bubble_max_chars',
        label: 'Caratteri max per bolla',
        kind: 'int',
        min: 80,
        max: 1000,
      },
    ],
  },
  {
    section: 'escalation',
    title: 'Escalation',
    description: 'Quando passare la chat a un operatore umano.',
    fields: [
      { key: 'escalation.enabled', label: 'Abilitata', kind: 'bool' },
      {
        key: 'escalation.handoff_message',
        label: 'Messaggio di passaggio',
        kind: 'textarea',
        placeholder: 'es. “Ti metto subito in contatto con un nostro operatore.” (vuoto = lascia scrivere al bot)',
      },
      { key: 'escalation.silent_handoff', label: 'Passaggio silenzioso (nessun messaggio al cliente)', kind: 'bool' },
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

// Sezioni nascoste dal pannello Configurazione. Le definizioni restano in
// SECTIONS sopra: per riattivarne una basta togliere la chiave da questo set.
// Le sezioni operative (no_answer/reactivation/scoring/booking) sono ora
// esposte: erano il gap percepito più grande rispetto alla console di Amalia.
// Restano nascoste solo quelle gestite altrove o puramente tecniche.
const HIDDEN_SECTIONS = new Set<string>([
  'business', // Spostato nella pagina dedicata "Brand → Informazioni"
  'rag', // RAG (UC-07) — parametri tecnici, gestiti dalla Knowledge Base
  'pipeline', // Pipeline (UC-04) — richiede gli ID GHL, gestiti dalle Integrazioni
]);

const VISIBLE_SECTIONS = SECTIONS.filter((s) => !HIDDEN_SECTIONS.has(s.section));

export function BotConfigPanel() {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);

  // Resolved server-side by the (app) layout via requireSession() and provided
  // through MerchantProvider — so it's populated both for a real merchant login
  // and for an agency impersonation session (token in the imp cookie, not in
  // the supabase-js browser session).
  const { merchantId } = useMerchantId();

  const resolvedQuery = useQuery({
    queryKey: ['bot-config', 'resolved', merchantId],
    enabled: !!merchantId,
    staleTime: 60_000,
    queryFn: async (): Promise<BotConfig> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/resolved', {
        params: { path: { merchant_id: merchantId! } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as BotConfig;
    },
  });

  const overridesQuery = useQuery({
    queryKey: ['bot-config', 'overrides', merchantId],
    enabled: !!merchantId,
    staleTime: 60_000,
    queryFn: async (): Promise<OverridesOut> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId! } },
      });
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
  // When the agency impersonates the merchant it owns the locks, so locked
  // fields become editable (the backend skips the lock-strip too).
  const isImpersonation = overridesQuery.data?.is_impersonation ?? false;

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
        '/bot-config/{merchant_id}/overrides',
        {
          params: { path: { merchant_id: merchantId! } },
          body: { overrides: nested },
        },
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

  // Apply a tone preset (dotted keys → values) and/or append a suggested rule,
  // routing through the same form/dirty machinery so Save persists them.
  const applyValues = (values: Record<string, unknown>) => {
    setForm((prev) => ({ ...prev, ...values }));
    setDirty((prev) => {
      const next = new Set(prev);
      for (const key of Object.keys(values)) next.add(key);
      return next;
    });
  };
  const appendPhrase = (key: 'bot.do_phrases' | 'bot.dont_phrases', phrase: string) => {
    setForm((prev) => {
      const current = Array.isArray(prev[key]) ? (prev[key] as string[]) : [];
      if (current.includes(phrase)) return prev;
      return { ...prev, [key]: [...current, phrase] };
    });
    setDirty((prev) => new Set(prev).add(key));
  };

  if (resolvedQuery.isLoading || overridesQuery.isLoading) {
    return (
      <div className="space-y-4 p-6">
        <SkeletonCard lines={2} />
        <SkeletonCard lines={6} />
        <SkeletonCard lines={6} />
      </div>
    );
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
        {isImpersonation ? (
          <p className="mt-2 text-violet-900">
            Stai configurando come agenzia: puoi modificare anche i campi bloccati
            (badge <span className="font-medium">🔓 Override agenzia</span>).
          </p>
        ) : null}
      </div>

      <PersonaPresets form={form} onApplyValues={applyValues} onAppendPhrase={appendPhrase} />

      {VISIBLE_SECTIONS.map((s) => (
        <SectionCard
          key={s.section}
          section={s}
          form={form}
          overridesFlat={overridesFlat}
          resolvedFlat={resolvedFlat}
          lockedSet={lockedSet}
          isImpersonation={isImpersonation}
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
          {save.isPending ? (
            <>
              <ButtonSpinner />
              Salvataggio…
            </>
          ) : (
            `Salva (${dirty.size})`
          )}
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
  isImpersonation,
  dirty,
  onChange,
  onReset,
}: {
  section: SectionDef;
  form: FormState;
  overridesFlat: FormState;
  resolvedFlat: FormState;
  lockedSet: Set<string>;
  isImpersonation: boolean;
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
          const lockedForMerchant = lockedSet.has(f.key);
          // The agency (impersonation) may edit locked fields; the merchant can't.
          const disabled = lockedForMerchant && !isImpersonation;
          const hasOverride = Object.prototype.hasOwnProperty.call(overridesFlat, f.key);
          const isDirty = dirty.has(f.key);
          const badge: BadgeKind = lockedForMerchant
            ? isImpersonation
              ? 'lock-override'
              : 'locked'
            : hasOverride || isDirty
              ? 'customized'
              : 'inherited';
          return (
            <FieldRow
              key={f.key}
              field={f}
              value={form[f.key]}
              inheritedValue={resolvedFlat[f.key]}
              badge={badge}
              locked={disabled}
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
  badge: BadgeKind;
  locked: boolean;
  isDirty: boolean;
  onChange: (v: unknown) => void;
  onReset: () => void;
}) {
  return (
    <div className="grid items-start gap-2 md:grid-cols-[1fr_auto] md:gap-4">
      <div className="flex flex-col gap-1">
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
        {field.help ? (
          <p className="text-xs text-muted-foreground">{field.help}</p>
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
      <Switch
        id={field.key}
        disabled={disabled}
        checked={!!value}
        onCheckedChange={(v) => onChange(v)}
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
  if (field.kind === 'select') {
    return (
      <select
        id={field.key}
        disabled={disabled}
        value={value === null || value === undefined ? '' : String(value)}
        onChange={(e) => onChange(e.target.value || null)}
        className="h-9 w-72 rounded-md border border-input bg-background px-3 text-sm"
      >
        {field.options?.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (field.kind === 'calendar') {
    return <CalendarFieldInput value={value} disabled={disabled} onChange={onChange} />;
  }
  if (field.kind === 'tags') {
    // Value is a string[]; render one item per line. Emit null when empty so
    // `inflate` drops it and the field reads as Inherited (not an empty override).
    const arr = Array.isArray(value) ? (value as unknown[]).map(String) : [];
    return (
      <textarea
        id={field.key}
        disabled={disabled}
        value={arr.join('\n')}
        onChange={(e) => {
          const lines = e.target.value
            .split('\n')
            .map((s) => s.trim())
            .filter(Boolean);
          onChange(lines.length ? lines : null);
        }}
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

function CalendarFieldInput({
  value,
  disabled,
  onChange,
}: {
  value: unknown;
  disabled: boolean;
  onChange: (v: unknown) => void;
}) {
  const { merchantId } = useMerchantId();
  const calendars = useQuery({
    queryKey: ['ghl', 'calendars', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<{ id: string; name: string | null }[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/integrations/ghl/calendars', {
        params: { query: { merchant_id: merchantId! } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as { calendars: { id: string; name: string | null }[] }).calendars;
    },
  });

  const current = value === null || value === undefined ? '' : String(value);
  const options = calendars.data ?? [];

  // GHL not connected (or no calendars): fall back to a manual id input so the
  // booking config still works without the picker.
  if (!calendars.isLoading && !calendars.isError && options.length === 0) {
    return (
      <input
        type="text"
        disabled={disabled}
        value={current}
        onChange={(e) => onChange(e.target.value || null)}
        placeholder="Calendar ID (GHL non collegato)"
        className="h-9 w-72 rounded-md border border-input bg-background px-3 text-sm"
      />
    );
  }

  const hasCurrent = options.some((c) => c.id === current);
  return (
    <select
      disabled={disabled || calendars.isLoading}
      value={current}
      onChange={(e) => onChange(e.target.value || null)}
      className="h-9 w-72 rounded-md border border-input bg-background px-3 text-sm"
    >
      <option value="">{calendars.isLoading ? 'Caricamento…' : '— Seleziona calendario —'}</option>
      {options.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name || c.id}
        </option>
      ))}
      {current && !hasCurrent ? <option value={current}>{current} (corrente)</option> : null}
    </select>
  );
}

function Badge({ kind }: { kind: BadgeKind }) {
  if (kind === 'locked') {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 ring-1 ring-inset ring-amber-200">
        🔒 Locked
      </span>
    );
  }
  if (kind === 'lock-override') {
    return (
      <span className="inline-flex items-center rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-900 ring-1 ring-inset ring-violet-200">
        🔓 Override agenzia
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

function PersonaPresets({
  form,
  onApplyValues,
  onAppendPhrase,
}: {
  form: FormState;
  onApplyValues: (values: Record<string, unknown>) => void;
  onAppendPhrase: (key: 'bot.do_phrases' | 'bot.dont_phrases', phrase: string) => void;
}) {
  const presetsQuery = useQuery({
    queryKey: ['bot-config', 'tone-presets'],
    staleTime: Infinity,
    queryFn: async (): Promise<TonePreset[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/tone-presets');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return (data as TonePreset[]) ?? [];
    },
  });
  const rulesQuery = useQuery({
    queryKey: ['bot-config', 'suggested-rules'],
    staleTime: Infinity,
    queryFn: async (): Promise<SuggestedRules> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/suggested-rules');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as SuggestedRules;
    },
  });

  const presets = presetsQuery.data ?? [];
  const rules = rulesQuery.data;
  const currentDo = Array.isArray(form['bot.do_phrases']) ? (form['bot.do_phrases'] as string[]) : [];
  const currentDont = Array.isArray(form['bot.dont_phrases'])
    ? (form['bot.dont_phrases'] as string[])
    : [];

  const isActive = (p: TonePreset) =>
    Object.entries(p.values as Record<string, unknown>).every(([k, v]) => form[k] === v);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Stile rapido</CardTitle>
        <p className="text-sm text-muted-foreground">
          Parti da un preset di tono, poi affina nei campi qui sotto. Le regole suggerite si
          aggiungono alle liste “da preferire / da evitare”.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Tono
          </p>
          <div className="flex flex-wrap gap-2">
            {presets.map((p) => {
              const active = isActive(p);
              return (
                <button
                  key={p.id}
                  type="button"
                  title={p.description}
                  onClick={() => onApplyValues(p.values as Record<string, unknown>)}
                  className={
                    'rounded-full border px-3 py-1 text-sm transition-colors ' +
                    (active
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-input hover:bg-accent')
                  }
                >
                  {p.label}
                </button>
              );
            })}
          </div>
        </div>

        {rules ? (
          <div className="grid gap-4 md:grid-cols-2">
            <RuleChips
              title="Regole da preferire"
              phrases={rules.do}
              current={currentDo}
              onAdd={(ph) => onAppendPhrase('bot.do_phrases', ph)}
            />
            <RuleChips
              title="Regole da evitare"
              phrases={rules.dont}
              current={currentDont}
              onAdd={(ph) => onAppendPhrase('bot.dont_phrases', ph)}
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RuleChips({
  title,
  phrases,
  current,
  onAdd,
}: {
  title: string;
  phrases: string[];
  current: string[];
  onAdd: (phrase: string) => void;
}) {
  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <div className="flex flex-wrap gap-2">
        {phrases.map((ph) => {
          const added = current.includes(ph);
          return (
            <button
              key={ph}
              type="button"
              disabled={added}
              onClick={() => onAdd(ph)}
              className={
                'rounded-full border px-3 py-1 text-left text-xs transition-colors ' +
                (added
                  ? 'cursor-default border-emerald-200 bg-emerald-50 text-emerald-700'
                  : 'border-dashed border-input hover:bg-accent')
              }
            >
              {added ? '✓ ' : '+ '}
              {ph}
            </button>
          );
        })}
      </div>
    </div>
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
