'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { getBrowserSupabase } from '@/lib/supabase';

type BotConfig = components['schemas']['BotConfigSchema'];

type OverrideBag = Record<string, Record<string, unknown>>;

async function loadMerchantId(): Promise<string | null> {
  const supabase = getBrowserSupabase();
  const { data } = await supabase.auth.getSession();
  const claims = (data.session?.user?.app_metadata as Record<string, unknown> | undefined) ?? {};
  return typeof claims.merchant_id === 'string' ? claims.merchant_id : null;
}

export function BotConfigPanel() {
  const queryClient = useQueryClient();
  const [overridesJson, setOverridesJson] = useState<string>('{}');
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

  const lockedKeys = useQuery({
    queryKey: ['bot-config', 'locked-keys'],
    queryFn: async (): Promise<string[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/templates' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      const templates = data as Array<{ is_default: boolean; locked_keys: string[] }>;
      const defaultTpl = templates.find((t) => t.is_default) ?? templates[0];
      return defaultTpl?.locked_keys ?? [];
    },
  });

  const save = useMutation({
    mutationFn: async (body: OverrideBag) => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const api = getApiClient();
      const { data, error } = await api.PUT(
        '/bot-config/{merchant_id}/overrides' as never,
        {
          params: { path: { merchant_id: merchantId } },
          body: { overrides: body },
        } as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bot-config', 'resolved', merchantId] });
      setOverridesJson('{}');
      setFormError(null);
    },
    onError: (err) => setFormError(err instanceof Error ? err.message : 'Errore salvataggio'),
  });

  const sections = useMemo(() => describeSections(resolvedQuery.data), [resolvedQuery.data]);

  if (merchantQuery.isLoading || resolvedQuery.isLoading) {
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
          I valori che vedi sono quelli <strong>risolti</strong> — cascata
          merchant → agenzia → sistema (§9). Per sovrascrivere un valore, usa
          l&apos;editor in fondo alla pagina: salva solo le chiavi che vuoi
          personalizzare.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {sections.map((s) => (
          <SectionCard
            key={s.key}
            section={s}
            lockedKeys={lockedKeys.data ?? []}
          />
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Override personalizzati</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Incolla solo le sezioni da sovrascrivere (es.{' '}
            <code className="rounded bg-muted px-1 py-0.5 text-xs">
              {'{"rag": {"top_k": 7}}'}
            </code>
            ). Le chiavi bloccate dall&apos;agenzia vengono ignorate lato backend.
          </p>
          <textarea
            rows={8}
            value={overridesJson}
            onChange={(e) => setOverridesJson(e.target.value)}
            className="block w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs"
            placeholder='{"rag": {"top_k": 7}}'
          />
          {formError ? <p className="text-sm text-destructive">{formError}</p> : null}
          <div className="flex justify-end">
            <Button
              disabled={save.isPending || overridesJson.trim() === ''}
              onClick={() => {
                setFormError(null);
                try {
                  const parsed = JSON.parse(overridesJson || '{}');
                  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
                    throw new Error('JSON deve essere un oggetto (es. {"rag": {"top_k": 7}}).');
                  }
                  save.mutate(parsed as OverrideBag);
                } catch (e) {
                  setFormError(e instanceof Error ? e.message : 'JSON non valido');
                }
              }}
            >
              {save.isPending ? 'Salvataggio…' : 'Salva override'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

type Section = {
  key: string;
  title: string;
  description: string;
  fields: Array<{ key: string; label: string; value: unknown }>;
};

function describeSections(resolved: BotConfig | undefined): Section[] {
  if (!resolved) return [];
  return [
    {
      key: 'no_answer',
      title: 'No answer (UC-03)',
      description: 'Follow-up se il lead non risponde.',
      fields: [
        { key: 'no_answer.first_reminder_min', label: '1° reminder (min)', value: resolved.no_answer?.first_reminder_min },
        { key: 'no_answer.second_reminder_min', label: '2° reminder (min)', value: resolved.no_answer?.second_reminder_min },
        { key: 'no_answer.max_followups', label: 'Max follow-up', value: resolved.no_answer?.max_followups },
      ],
    },
    {
      key: 'reactivation',
      title: 'Reactivation (UC-06)',
      description: 'Riattivazione lead dormienti.',
      fields: [
        { key: 'reactivation.dormant_days', label: 'Giorni dormienza', value: resolved.reactivation?.dormant_days },
        { key: 'reactivation.interval_days', label: 'Intervallo tentativi (giorni)', value: resolved.reactivation?.interval_days },
        { key: 'reactivation.max_attempts', label: 'Max tentativi', value: resolved.reactivation?.max_attempts },
      ],
    },
    {
      key: 'scoring',
      title: 'Scoring (UC-05)',
      description: 'Soglie per classificare hot / cold.',
      fields: [
        { key: 'scoring.hot_threshold', label: 'Hot threshold', value: resolved.scoring?.hot_threshold },
        { key: 'scoring.cold_threshold', label: 'Cold threshold', value: resolved.scoring?.cold_threshold },
      ],
    },
    {
      key: 'rag',
      title: 'RAG (UC-07)',
      description: 'Retrieval dalla knowledge base.',
      fields: [
        { key: 'rag.top_k', label: 'Top K', value: resolved.rag?.top_k },
        { key: 'rag.min_score', label: 'Soglia minima', value: resolved.rag?.min_score },
      ],
    },
    {
      key: 'schedule',
      title: 'Orari & lingua',
      description: 'Orari attivi, messaggio fuori orario, lingua del bot.',
      fields: [
        { key: 'schedule.active_hours', label: 'Orari attivi', value: resolved.schedule?.active_hours },
        { key: 'schedule.timezone', label: 'Timezone', value: resolved.schedule?.timezone },
        { key: 'bot.language', label: 'Lingua bot', value: resolved.bot?.language },
        { key: 'bot.tone', label: 'Tono bot', value: resolved.bot?.tone },
      ],
    },
    {
      key: 'booking',
      title: 'Booking (UC-02)',
      description: 'Default calendario e durata appuntamenti.',
      fields: [
        { key: 'booking.default_duration_min', label: 'Durata default (min)', value: resolved.booking?.default_duration_min },
        { key: 'booking.lookahead_days', label: 'Lookahead (giorni)', value: resolved.booking?.lookahead_days },
        { key: 'booking.default_calendar_id', label: 'Calendar ID default', value: resolved.booking?.default_calendar_id ?? '—' },
      ],
    },
  ];
}

function SectionCard({ section, lockedKeys }: { section: Section; lockedKeys: string[] }) {
  const hasLocked = section.fields.some((f) => lockedKeys.includes(f.key));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {section.title}
          {hasLocked ? <LockedBadge /> : null}
        </CardTitle>
        <p className="text-sm text-muted-foreground">{section.description}</p>
      </CardHeader>
      <CardContent>
        <dl className="space-y-2 text-sm">
          {section.fields.map((f) => {
            const locked = lockedKeys.includes(f.key);
            return (
              <div key={f.key} className="flex items-center justify-between gap-4">
                <dt className="text-muted-foreground">
                  {f.label}
                  {locked ? <span className="ml-2 text-xs font-normal">🔒</span> : null}
                </dt>
                <dd className="font-mono text-xs">{formatValue(f.value)}</dd>
              </div>
            );
          })}
        </dl>
      </CardContent>
    </Card>
  );
}

function LockedBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 ring-1 ring-inset ring-amber-200">
      Contiene chiavi bloccate
    </span>
  );
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}
