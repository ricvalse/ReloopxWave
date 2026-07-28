'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  KPICard,
  Tabs,
  TabsList,
  TabsTrigger,
  toast,
} from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import { MetricBuilder } from './metric-builder';
import { OutcomesEditor } from './outcomes-editor';
import { ProfilesEditor } from './profiles-editor';
import type { ConversationProfile, MetricDefinition, MetricValue } from './types';

const PERIODS = [
  { value: 7, label: 'Ultimi 7 giorni' },
  { value: 30, label: 'Ultimi 30 giorni' },
  { value: 90, label: 'Ultimi 90 giorni' },
];

/** Valore speciale del selettore: nessun profilo → configurazione del merchant. */
const ALL_PROFILES = '__merchant__';

export function StatistichePanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [sinceDays, setSinceDays] = useState(30);
  const [profileId, setProfileId] = useState<string>(ALL_PROFILES);
  const [tab, setTab] = useState<'bolle' | 'profili' | 'statistiche'>('bolle');

  const activeProfileId = profileId === ALL_PROFILES ? undefined : profileId;

  const profilesQuery = useQuery({
    queryKey: ['conversation-profiles', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<ConversationProfile[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/statistics/profiles');
      if (error) throw new Error(JSON.stringify(error));
      return (data ?? []) as ConversationProfile[];
    },
  });

  // Le bolle NON sono cablate qui: arrivano dal config cascade già risolte e
  // conteggiate. Con `profile_id` la risoluzione passa per il livello 0 della
  // cascata, quindi cambia sia QUALI bolle vedi sia SU QUALI dati.
  const metricsQuery = useQuery({
    queryKey: ['statistics-metrics', merchantId, sinceDays, activeProfileId],
    enabled: !!merchantId,
    queryFn: async (): Promise<MetricValue[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/metrics', {
        params: {
          query: {
            since_days: sinceDays,
            ...(activeProfileId ? { profile_id: activeProfileId } : {}),
          },
        },
      });
      if (error) throw new Error(JSON.stringify(error));
      return (data?.metrics ?? []) as MetricValue[];
    },
    refetchInterval: 60_000,
  });

  /** Le definizioni correnti: dal profilo se ne ha di proprie, altrimenti dal merchant. */
  const definitionsQuery = useQuery({
    queryKey: ['statistics-definitions', merchantId, activeProfileId],
    enabled: !!merchantId,
    queryFn: async (): Promise<MetricDefinition[]> => {
      const api = getApiClient();
      if (activeProfileId) {
        const profile = (profilesQuery.data ?? []).find((p) => p.id === activeProfileId);
        const fromProfile = readMetrics(profile?.overrides);
        if (fromProfile) return fromProfile;
      }
      const { data, error } = await api.GET('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId! } },
      });
      if (error) throw new Error(JSON.stringify(error));
      return readMetrics(data?.overrides) ?? [];
    },
  });

  const saveMutation = useMutation({
    mutationFn: async (metrics: MetricDefinition[]) => {
      const api = getApiClient();
      if (activeProfileId) {
        // Le bolle del profilo vivono nei suoi override: è il livello 0 della
        // cascata, quindi un profilo senza `dashboard.metrics` eredita quelle
        // del merchant senza doverle duplicare.
        const profile = (profilesQuery.data ?? []).find((p) => p.id === activeProfileId);
        const overrides = { ...(profile?.overrides ?? {}) } as Record<string, unknown>;
        overrides.dashboard = { ...(asRecord(overrides.dashboard) ?? {}), metrics };
        const { error } = await api.PATCH('/statistics/profiles/{profile_id}', {
          params: { path: { profile_id: activeProfileId } },
          body: { overrides },
        });
        if (error) throw new Error(JSON.stringify(error));
        return;
      }
      const { data: current, error: readError } = await api.GET(
        '/bot-config/{merchant_id}/overrides',
        { params: { path: { merchant_id: merchantId! } } },
      );
      if (readError) throw new Error(JSON.stringify(readError));
      const overrides = { ...(current?.overrides ?? {}) } as Record<string, unknown>;
      overrides.dashboard = { ...(asRecord(overrides.dashboard) ?? {}), metrics };
      const { error } = await api.PUT('/bot-config/{merchant_id}/overrides', {
        params: { path: { merchant_id: merchantId! } },
        body: { overrides },
      });
      if (error) throw new Error(JSON.stringify(error));
    },
    onSuccess: () => {
      toast.success('Statistiche aggiornate');
      void queryClient.invalidateQueries({ queryKey: ['statistics-metrics'] });
      void queryClient.invalidateQueries({ queryKey: ['statistics-definitions'] });
      void queryClient.invalidateQueries({ queryKey: ['conversation-profiles'] });
    },
    onError: () => toast.error('Salvataggio non riuscito'),
  });

  const metrics = metricsQuery.data;
  const { automatiche, personalizzate } = useMemo(() => splitBySource(metrics ?? []), [metrics]);
  const profiles = profilesQuery.data ?? [];
  const selectedProfile = profiles.find((p) => p.id === activeProfileId);
  const profileHasOwnMetrics = !!readMetrics(selectedProfile?.overrides);

  return (
    <div className="space-y-4 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label="Profilo di conversazione"
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option value={ALL_PROFILES}>Tutti i profili (merchant)</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {p.is_default ? ' — default' : ''}
            </option>
          ))}
        </select>
        <select
          aria-label="Periodo"
          value={sinceDays}
          onChange={(e) => setSinceDays(Number(e.target.value))}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
        {activeProfileId ? (
          <Badge variant={profileHasOwnMetrics ? 'default' : 'secondary'}>
            {profileHasOwnMetrics ? 'Bolle proprie del profilo' : 'Eredita dal merchant'}
          </Badge>
        ) : null}
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
        <TabsList>
          <TabsTrigger value="bolle">Bolle</TabsTrigger>
          <TabsTrigger value="profili">Profili</TabsTrigger>
          <TabsTrigger value="statistiche">Statistiche personalizzate</TabsTrigger>
        </TabsList>
      </Tabs>

      {tab === 'bolle' ? (
        <>
          <MetricGrid
            title="Automatiche"
            hint="Escono dai dati che il sistema registra da solo. Nessuna configurazione richiesta."
            metrics={automatiche}
            loading={metricsQuery.isLoading}
            sinceDays={sinceDays}
          />
          <MetricGrid
            title="Personalizzate"
            hint="Contano un esito che dichiari tu e che un'automazione registra con un nodo «Registra esito»."
            metrics={personalizzate}
            loading={metricsQuery.isLoading}
            sinceDays={sinceDays}
            emptyHint="Nessuna statistica personalizzata sulla dashboard. Creane una nella scheda «Statistiche personalizzate», poi aggiungila qui sotto."
          />
          <MetricBuilder
            definitions={definitionsQuery.data ?? []}
            saving={saveMutation.isPending}
            scopeLabel={selectedProfile ? `profilo «${selectedProfile.name}»` : 'merchant'}
            onSave={(next) => saveMutation.mutate(next)}
          />
        </>
      ) : null}

      {tab === 'profili' ? <ProfilesEditor profiles={profiles} /> : null}
      {tab === 'statistiche' ? <OutcomesEditor /> : null}
    </div>
  );
}

function MetricGrid({
  title,
  hint,
  metrics,
  loading,
  sinceDays,
  emptyHint,
}: {
  title: string;
  hint: string;
  metrics: MetricValue[];
  loading: boolean;
  sinceDays: number;
  emptyHint?: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3 lg:grid-cols-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <KPICard key={i} label="" value="—" loading />
            ))}
          </div>
        ) : metrics.length === 0 ? (
          <p className="text-sm text-muted-foreground">{emptyHint ?? 'Nessuna bolla in questa sezione.'}</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3 lg:grid-cols-4">
            {metrics.map((m) => (
              <KPICard
                key={m.id}
                label={m.window_days === sinceDays ? m.label : `${m.label} (${m.window_days}g)`}
                value={m.value}
                // Un numero inferito da un `ai_check` non è un fatto: la bolla
                // deve poter dire di che pasta è fatto.
                hint={
                  m.source === 'outcome' && typeof m.verified === 'number' && m.verified < m.value
                    ? `di cui ${m.verified} verificati`
                    : undefined
                }
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function splitBySource(metrics: MetricValue[]) {
  return {
    automatiche: metrics.filter((m) => m.source !== 'outcome'),
    personalizzate: metrics.filter((m) => m.source === 'outcome'),
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** Legge `dashboard.metrics` da un bag di override (nested o flat). */
function readMetrics(overrides: unknown): MetricDefinition[] | null {
  const bag = asRecord(overrides);
  if (!bag) return null;
  const nested = asRecord(bag.dashboard)?.metrics;
  if (Array.isArray(nested)) return nested as MetricDefinition[];
  const flat = bag['dashboard.metrics'];
  return Array.isArray(flat) ? (flat as MetricDefinition[]) : null;
}
