'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, LockOpen, RotateCcw } from 'lucide-react';
import type { components } from '@reloop/api-client';
import {
  Badge,
  Button,
  ButtonSpinner,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  SkeletonCard,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import { FieldInput } from './field-input';
import { PersonaPresets } from './persona-presets';
import {
  STACKED_KINDS,
  VISIBLE_SECTIONS,
  type BadgeKind,
  type FieldDef,
  type SectionDef,
} from './sections';

type BotConfig = components['schemas']['BotConfigSchema'];
type OverridesOut = components['schemas']['OverridesOut'];

type OverrideBag = Record<string, Record<string, unknown>>;
type FormState = Record<string, unknown>; // flat, dotted keys

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
      const { data, error } = await api.PUT('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId! } },
        body: { overrides: nested },
      });
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

  // How many unsaved edits sit in each section, so the index can point at them.
  const dirtyBySection = useMemo(() => {
    const counts = new Map<string, number>();
    for (const key of dirty) {
      const section = key.split('.')[0] ?? '';
      counts.set(section, (counts.get(section) ?? 0) + 1);
    }
    return counts;
  }, [dirty]);

  const activeSection = useActiveSection(VISIBLE_SECTIONS.map((s) => s.section));

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
    <div className="p-6 pb-24">
      <div className="mx-auto grid max-w-6xl gap-8 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <SectionIndex
          sections={VISIBLE_SECTIONS}
          activeSection={activeSection}
          dirtyBySection={dirtyBySection}
        />

        {/* Capped, not full-bleed: at 1920px a `1fr auto` row used to strand the
            label at the far left and the control at the far right with ~900px of
            nothing in between, and the eye lost the pairing. */}
        <div className="min-w-0 space-y-5">
          {isImpersonation ? (
            <div className="flex items-start gap-2 rounded-md border border-primary/40 bg-card-elevated px-4 py-3 text-sm">
              <LockOpen className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <p className="text-muted-foreground">
                Stai configurando come agenzia: puoi modificare anche i campi che il merchant
                vede bloccati.
              </p>
            </div>
          ) : null}

          <PersonaPresets
            form={form}
            onApplyValues={applyValues}
            onAppendPhrase={appendPhrase}
          />

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
        </div>
      </div>

      <SaveBar
        count={dirty.size}
        saving={save.isPending}
        error={formError}
        onSave={() => save.mutate()}
        onDiscard={resetAll}
      />
    </div>
  );
}

/**
 * Sticky index of the sections, with the unsaved-edit count per section.
 *
 * 44 fields in one scroll with no way to jump meant reaching "Passaggio a
 * operatore" required scrolling past forty controls you weren't looking for.
 */
function SectionIndex({
  sections,
  activeSection,
  dirtyBySection,
}: {
  sections: SectionDef[];
  activeSection: string | null;
  dirtyBySection: Map<string, number>;
}) {
  return (
    <nav aria-label="Sezioni" className="hidden lg:block">
      <ul className="sticky top-6 space-y-0.5 text-sm">
        {sections.map((s) => {
          const active = activeSection === s.section;
          const pending = dirtyBySection.get(s.section) ?? 0;
          return (
            <li key={s.section}>
              <a
                href={`#${s.section}`}
                aria-current={active ? 'true' : undefined}
                className={
                  'flex items-center justify-between gap-2 rounded-md px-3 py-1.5 transition-colors ' +
                  (active
                    ? 'bg-accent font-medium text-accent-foreground'
                    : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground')
                }
              >
                <span className="truncate">{s.nav}</span>
                {pending > 0 ? (
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary"
                    aria-label={`${pending} modifiche non salvate`}
                  />
                ) : null}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/**
 * Which section the reader is currently looking at, for the index highlight.
 * Plain IntersectionObserver on the section headings — no scroll listener, so
 * nothing runs while the page is still.
 */
function useActiveSection(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(ids[0] ?? null);
  // The id list is rebuilt on every render by the caller's `.map()`; key the
  // effect on its contents so the observer isn't torn down every time.
  const key = ids.join(',');
  const idsRef = useRef(ids);
  idsRef.current = ids;

  useEffect(() => {
    const elements = idsRef.current
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (elements.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      // Top-biased band: a section counts as "current" once its heading reaches
      // the upper third, which is where you read, not where the viewport starts.
      { rootMargin: '-10% 0px -70% 0px', threshold: 0 },
    );
    for (const el of elements) observer.observe(el);
    return () => observer.disconnect();
  }, [key]);

  return active;
}

/**
 * Action bar pinned to the bottom of the viewport while there are unsaved edits.
 * The buttons used to live below the last of ten cards: you had to scroll to the
 * end of the page to save a change made at the top of it.
 */
function SaveBar({
  count,
  saving,
  error,
  onSave,
  onDiscard,
}: {
  count: number;
  saving: boolean;
  error: string | null;
  onSave: () => void;
  onDiscard: () => void;
}) {
  const visible = count > 0 || saving || !!error;
  if (!visible) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-20 p-4">
      <div className="pointer-events-auto mx-auto flex max-w-2xl flex-wrap items-center gap-3 rounded-lg border bg-card-elevated px-4 py-3 shadow-lg">
        <p className="min-w-0 flex-1 text-sm">
          {error ? (
            <span className="text-destructive">{error}</span>
          ) : (
            <>
              <span className="font-medium">
                {count} {count === 1 ? 'modifica' : 'modifiche'}
              </span>
              <span className="text-muted-foreground"> da salvare</span>
            </>
          )}
        </p>
        <Button variant="ghost" size="sm" onClick={onDiscard} disabled={count === 0 || saving}>
          Scarta
        </Button>
        <Button size="sm" onClick={onSave} disabled={count === 0 || saving}>
          {saving ? (
            <>
              <ButtonSpinner />
              Salvataggio…
            </>
          ) : (
            'Salva'
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
    // `scroll-mt` so the index anchors don't park the heading under the top edge.
    <Card id={section.section} className="scroll-mt-6">
      <CardHeader>
        <CardTitle>{section.title}</CardTitle>
        <p className="text-sm text-muted-foreground">{section.description}</p>
      </CardHeader>
      <CardContent className="divide-y divide-border/60 py-0">
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
  // Textareas, tag lists and checkbox groups need the full width and go under
  // the label; switches, numbers and selects sit beside it. Mixing the two in
  // one right-aligned column was what made the right edge look ragged.
  const stacked = STACKED_KINDS.has(field.kind);

  const label = (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <label htmlFor={field.key} className="text-sm font-medium">
          {field.label}
        </label>
        <StatusBadge kind={badge} inheritedValue={inheritedValue} />
        {isDirty ? (
          <button
            type="button"
            onClick={onReset}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <RotateCcw className="h-3 w-3" />
            Annulla
          </button>
        ) : null}
      </div>
      {field.help ? (
        <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">{field.help}</p>
      ) : null}
    </div>
  );

  if (stacked) {
    return (
      <div className="space-y-2 py-4">
        {label}
        <FieldInput
          field={field}
          value={value}
          disabled={locked}
          onChange={onChange}
          placeholder={inheritedPlaceholder(field, inheritedValue)}
        />
      </div>
    );
  }

  return (
    <div className="grid items-center gap-x-6 gap-y-2 py-4 sm:grid-cols-[minmax(0,1fr)_14rem]">
      {label}
      <div className="sm:justify-self-end">
        <FieldInput
          field={field}
          value={value}
          disabled={locked}
          onChange={onChange}
          placeholder={inheritedPlaceholder(field, inheritedValue)}
        />
      </div>
    </div>
  );
}

function inheritedPlaceholder(field: FieldDef, inheritedValue: unknown): string | undefined {
  if (field.kind !== 'text') return undefined;
  if (inheritedValue === null || inheritedValue === undefined) return undefined;
  return String(inheritedValue);
}

/**
 * Where a value comes from, in the language the rest of the UI speaks.
 *
 * The old badges said "Inherited" / "Customized" / "Locked" inside an otherwise
 * entirely Italian product, and — worse — never showed what the inherited value
 * actually was, so "Ereditato" told you nothing you could act on. The tooltip
 * carries the value.
 */
function StatusBadge({
  kind,
  inheritedValue,
}: {
  kind: BadgeKind;
  inheritedValue: unknown;
}) {
  if (kind === 'locked') {
    return (
      <WithTooltip label="Bloccato dall'agenzia: solo loro possono cambiarlo.">
        <Badge variant="warning" className="gap-1">
          <Lock className="h-3 w-3" />
          Bloccato
        </Badge>
      </WithTooltip>
    );
  }
  if (kind === 'lock-override') {
    return (
      <WithTooltip label="Bloccato per il merchant. Come agenzia puoi modificarlo.">
        <Badge variant="secondary" className="gap-1">
          <LockOpen className="h-3 w-3" />
          Bloccato per il merchant
        </Badge>
      </WithTooltip>
    );
  }
  if (kind === 'customized') {
    return (
      <WithTooltip label={describeInherited(inheritedValue)}>
        <Badge variant="default">Personalizzato</Badge>
      </WithTooltip>
    );
  }
  return (
    <WithTooltip label="Valore di default: arriva dall'agenzia o dal sistema.">
      <Badge variant="outline" className="text-muted-foreground">
        Ereditato
      </Badge>
    </WithTooltip>
  );
}

function describeInherited(inheritedValue: unknown): string {
  if (inheritedValue === null || inheritedValue === undefined || inheritedValue === '') {
    return 'Valore tuo, diverso da quello ereditato (il default è vuoto).';
  }
  const text = Array.isArray(inheritedValue)
    ? inheritedValue.map(String).join(', ')
    : String(inheritedValue);
  const short = text.length > 120 ? `${text.slice(0, 119)}…` : text;
  return `Valore tuo. Senza personalizzazione erediteresti: ${short}`;
}

function WithTooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help">{children}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{label}</TooltipContent>
    </Tooltip>
  );
}

// ---- helpers --------------------------------------------------------------

function flatten(obj: Record<string, unknown>, prefix = '', out: FormState = {}): FormState {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
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
