'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Badge, Button, Input, Label, Textarea, toast, useListDraft } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import { flatten, inflate, type FormState } from '@/components/bot-config/override-bag';
import type { ConversationProfile } from './types';

/** Le azioni che il bot può eseguire. Stesso elenco del pannello bot-config:
 *  su un profilo non commerciale (es. colloqui) si lascia tipicamente solo
 *  «Passa a operatore». */
const ACTION_OPTIONS = [
  { value: 'check_availability', label: 'Verifica disponibilità' },
  { value: 'lookup_appointment', label: 'Cerca appuntamento' },
  { value: 'propose_slots', label: 'Proponi disponibilità' },
  { value: 'book_slot', label: 'Prenota appuntamento' },
  { value: 'reschedule_slot', label: 'Sposta appuntamento' },
  { value: 'cancel_slot', label: 'Annulla appuntamento' },
  { value: 'move_pipeline', label: 'Avanza in pipeline' },
  { value: 'update_score', label: 'Aggiorna scoring' },
  { value: 'escalate_human', label: 'Passa a operatore' },
];

type FieldKind = 'select' | 'textarea' | 'lines' | 'multiselect' | 'text';

type FieldDef = {
  key: string;
  label: string;
  kind: FieldKind;
  help?: string;
  placeholder?: string;
  rows?: number;
  options?: { value: string; label: string }[];
};

/** Solo i knob che ADR 0022 mette nello scope V1 di un profilo: comportamento e
 *  voce. Booking, scoring, RAG e la scelta del modello restano del merchant e
 *  non sono sovrascrivibili — un profilo cambia *come parla* il bot, non su
 *  quale calendario prenota. */
const SECTIONS: { title: string; description: string; fields: FieldDef[] }[] = [
  {
    title: 'Comportamento',
    description:
      'Cosa deve fare il bot quando questa conversazione usa il profilo. Tutto il resto — informazioni aziendali, knowledge base, prenotazioni — resta quello del merchant.',
    fields: [
      {
        key: 'conversation.playbook.mode',
        label: 'Modalità',
        kind: 'select',
        options: [
          { value: 'fsm_legacy', label: 'Vendita (qualifica → offerta → prenotazione)' },
          { value: 'off', label: 'Solo direttive (nessuna qualifica)' },
        ],
        help: 'Per un profilo non commerciale — colloqui, assistenza — scegli «Solo direttive»: spegne gli automatismi di vendita e lascia comandare le regole qui sotto.',
      },
      {
        key: 'conversation.playbook.goal',
        label: 'Obiettivo',
        kind: 'textarea',
        rows: 2,
        placeholder:
          'es. Raccogliere disponibilità e motivazione del candidato per la posizione aperta.',
      },
      {
        key: 'conversation.playbook.directives',
        label: 'Regole della conversazione',
        kind: 'lines',
        rows: 5,
        help: 'Una regola per riga. Hanno priorità su tutto il resto. Es. «Non proporre mai appuntamenti commerciali», «Se il candidato chiede lo stipendio, rimanda al colloquio».',
      },
      {
        key: 'conversation.playbook.actions.enabled',
        label: 'Azioni permesse',
        kind: 'multiselect',
        options: ACTION_OPTIONS,
        help: 'Nessuna selezione = tutte permesse. Per un profilo di colloqui lascia tipicamente solo «Passa a operatore».',
      },
    ],
  },
  {
    title: 'Voce',
    description: 'Come suona il bot su questo profilo.',
    fields: [
      {
        key: 'bot.system_prompt_additions',
        label: 'Istruzioni aggiuntive',
        kind: 'textarea',
        rows: 4,
        placeholder:
          'Testo libero aggiunto al prompt di sistema quando la conversazione usa questo profilo.',
      },
      {
        key: 'bot.tone',
        label: 'Tono',
        kind: 'text',
        placeholder: 'es. professionale-amichevole',
      },
    ],
  },
];

const ALL_KEYS = SECTIONS.flatMap((s) => s.fields.map((f) => f.key));

/**
 * Scrive il **delta** di un profilo sopra la configurazione del merchant.
 *
 * Un campo non impostato qui non è "vuoto": è **ereditato**. La distinzione è
 * quella che rende il profilo un delta e non un secondo bot da riconfigurare da
 * zero, quindi va mostrata per campo — con il valore ereditato in chiaro, così
 * si vede cosa si sta sovrascrivendo prima di farlo.
 */
export function ProfileBehaviorEditor({ profile }: { profile: ConversationProfile }) {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>({});
  const [dirty, setDirty] = useState(false);

  const overrideState = useMemo(
    () => flatten(profile.overrides as Record<string, unknown>),
    [profile.overrides],
  );

  useEffect(() => {
    if (!dirty) setForm(overrideState);
  }, [overrideState, dirty]);

  // Config del merchant già risolta: è ciò che il profilo eredita quando non
  // sovrascrive.
  const inheritedQuery = useQuery({
    queryKey: ['bot-config-resolved', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<FormState> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/{merchant_id}/resolved', {
        params: { path: { merchant_id: merchantId! } },
      });
      if (error) throw new Error(JSON.stringify(error));
      return flatten((data ?? {}) as Record<string, unknown>);
    },
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      // Si conservano le chiavi che questo pannello non gestisce (es.
      // `dashboard.metrics`, scritto dal builder delle bolle): il profilo è un
      // bag unico e questo form ne governa solo una fetta.
      const untouched: FormState = Object.fromEntries(
        Object.entries(overrideState).filter(
          ([k]) => !ALL_KEYS.some((known) => k === known || k.startsWith(`${known}.`)),
        ),
      );
      const { error } = await api.PATCH('/statistics/profiles/{profile_id}', {
        params: { path: { profile_id: profile.id } },
        body: { overrides: inflate({ ...untouched, ...form }) },
      });
      if (error) throw new Error(JSON.stringify(error));
    },
    onSuccess: () => {
      toast.success('Profilo aggiornato');
      setDirty(false);
      void queryClient.invalidateQueries({ queryKey: ['conversation-profiles'] });
    },
    onError: () => toast.error('Salvataggio non riuscito'),
  });

  const setValue = (key: string, value: unknown) => {
    setDirty(true);
    setForm((prev) => ({ ...prev, [key]: value }));
  };
  const resetToInherited = (key: string) => {
    setDirty(true);
    setForm((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const inherited = inheritedQuery.data ?? {};

  return (
    <div className="space-y-5 rounded-md border border-border bg-muted/30 p-4">
      {SECTIONS.map((section) => (
        <section key={section.title} className="space-y-3">
          <div>
            <h4 className="text-sm font-medium">{section.title}</h4>
            <p className="text-xs text-muted-foreground">{section.description}</p>
          </div>
          {section.fields.map((field) => {
            const isOverridden = Object.prototype.hasOwnProperty.call(form, field.key);
            const value = isOverridden ? form[field.key] : inherited[field.key];
            return (
              <div key={field.key} className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Label className="text-xs" htmlFor={`${profile.id}-${field.key}`}>
                    {field.label}
                  </Label>
                  <Badge variant={isOverridden ? 'default' : 'secondary'}>
                    {isOverridden ? 'Personalizzato' : 'Ereditato'}
                  </Badge>
                  {isOverridden ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2 text-xs"
                      onClick={() => resetToInherited(field.key)}
                    >
                      Torna a ereditato
                    </Button>
                  ) : null}
                </div>
                <FieldInput
                  id={`${profile.id}-${field.key}`}
                  field={field}
                  value={value}
                  onChange={(v) => setValue(field.key, v)}
                />
                {field.help ? (
                  <p className="text-xs text-muted-foreground">{field.help}</p>
                ) : null}
              </div>
            );
          })}
        </section>
      ))}

      <div className="flex items-center gap-2">
        <Button onClick={() => saveMutation.mutate()} disabled={!dirty || saveMutation.isPending}>
          {saveMutation.isPending ? 'Salvataggio…' : 'Salva profilo'}
        </Button>
        {dirty ? (
          <Button
            variant="ghost"
            onClick={() => {
              setForm(overrideState);
              setDirty(false);
            }}
          >
            Annulla
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function FieldInput({
  id,
  field,
  value,
  onChange,
}: {
  id: string;
  field: FieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const selectClass = 'h-9 w-full rounded-md border border-input bg-background px-2 text-sm';

  if (field.kind === 'select') {
    return (
      <select
        id={id}
        className={selectClass}
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
      >
        {(field.options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }

  if (field.kind === 'textarea') {
    return (
      <Textarea
        id={id}
        rows={field.rows ?? 3}
        placeholder={field.placeholder}
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (field.kind === 'lines') {
    return <LinesFieldInput id={id} field={field} value={value} onChange={onChange} />;
  }

  if (field.kind === 'multiselect') {
    const selected = Array.isArray(value) ? (value as string[]) : [];
    return (
      <div className="flex flex-wrap gap-1.5">
        {(field.options ?? []).map((o) => {
          const on = selected.includes(o.value);
          return (
            <button
              key={o.value}
              type="button"
              onClick={() =>
                onChange(
                  on ? selected.filter((v) => v !== o.value) : [...selected, o.value],
                )
              }
              className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                on
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-input bg-background text-muted-foreground hover:bg-accent'
              }`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <Input
      id={id}
      placeholder={field.placeholder}
      value={String(value ?? '')}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/**
 * Una regola per riga: le direttive sono una lista lato backend, e mostrarle
 * come righe evita che il merchant debba pensare in termini di array.
 *
 * Il testo passa da `useListDraft`, così lo spazio a fine parola e la riga
 * aperta con Invio restano dove sono stati battuti — prima la lista ripulita
 * rientrava nel `value` a ogni tasto e il campo era inscrivibile. Stesso difetto
 * e stesso rimedio del pannello Configurazione bot: i profili sono un delta
 * della medesima `BotConfigSchema`.
 *
 * Componente a sé — e non un ramo di `FieldInput` — perché un hook non può
 * stare dentro un `if`.
 */
function LinesFieldInput({
  id,
  field,
  value,
  onChange,
}: {
  id: string;
  field: FieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  // Una stringa arriva solo da un valore storico: spezzarla per righe la mostra
  // esattamente com'era e la normalizza al primo tocco.
  const items = Array.isArray(value)
    ? (value as unknown[]).map(String)
    : value
      ? String(value).split('\n')
      : [];
  const { text, setText } = useListDraft({ items, separator: '\n', join: '\n', onChange });

  return (
    <Textarea
      id={id}
      rows={field.rows ?? 4}
      placeholder={field.placeholder}
      value={text}
      onChange={(e) => setText(e.target.value)}
    />
  );
}
