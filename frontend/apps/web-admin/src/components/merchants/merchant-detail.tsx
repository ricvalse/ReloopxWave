'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle, KPICard } from '@reloop/ui';
import { toast } from '@reloop/ui';
import { getApiClient, apiFetch } from '@/lib/api';
import { InviteUserCard } from './invite-user-card';
import { StatusBadge } from './status-badge';

type Template = components['schemas']['TemplateOut'];

type Merchant = components['schemas']['MerchantOut'];
type Kpis = components['schemas']['MerchantKpisOut'];

export function MerchantDetail({ merchantId }: { merchantId: string }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['merchants', merchantId],
    queryFn: async (): Promise<Merchant> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/merchants/{merchant_id}', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
  });

  const invalidateBoth = () => {
    void queryClient.invalidateQueries({ queryKey: ['merchants', merchantId] });
    void queryClient.invalidateQueries({ queryKey: ['merchants', 'list'] });
  };

  const suspend = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { data, error } = await api.POST('/merchants/{merchant_id}/suspend', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
    onSuccess: invalidateBoth,
    onError: (err) => setMutationError(err instanceof Error ? err.message : 'Errore sospensione'),
  });

  const resume = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { data, error } = await api.POST('/merchants/{merchant_id}/resume', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
    onSuccess: invalidateBoth,
    onError: (err) => setMutationError(err instanceof Error ? err.message : 'Errore riattivazione'),
  });

  const remove = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { error } = await api.DELETE('/merchants/{merchant_id}', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['merchants', 'list'] });
      queryClient.removeQueries({ queryKey: ['merchants', merchantId] });
      router.push('/merchants');
    },
    onError: (err) => setMutationError(err instanceof Error ? err.message : 'Errore eliminazione'),
  });

  const impersonate = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { data, error } = await api.POST('/admin/impersonation/{merchant_id}', {
        params: { path: { merchant_id: merchantId } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as {
        access_token: string;
        expires_at: number;
        merchant_name: string;
        web_merchant_url: string;
        session_id: string;
      };
    },
    onSuccess: (res) => {
      const base = (process.env.NEXT_PUBLIC_WEB_MERCHANT_URL ?? res.web_merchant_url ?? '').replace(
        /\/$/,
        '',
      );
      if (!base) {
        setMutationError('URL del portale merchant non configurato.');
        return;
      }
      const url = `${base}/impersonate#token=${encodeURIComponent(res.access_token)}&exp=${res.expires_at}`;
      // New tab, no opener handle (anti reverse-tabnabbing).
      window.open(url, '_blank', 'noopener');
    },
    onError: (err) =>
      setMutationError(err instanceof Error ? err.message : 'Errore impersonazione'),
  });

  const saveName = useMutation({
    mutationFn: async (name: string) => {
      const api = getApiClient();
      const { data, error } = await api.PATCH('/merchants/{merchant_id}', {
        params: { path: { merchant_id: merchantId } },
        body: { name },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Merchant;
    },
    onSuccess: () => {
      setNameDraft(null);
      invalidateBoth();
    },
    onError: (err) => setMutationError(err instanceof Error ? err.message : 'Errore salvataggio'),
  });

  if (query.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">Caricamento merchant…</div>;
  }

  if (query.isError || !query.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        Impossibile caricare il merchant.{' '}
        {query.error instanceof Error ? query.error.message : ''}
      </div>
    );
  }

  const m = query.data;
  const isSuspended = m.status === 'suspended';
  const pending =
    suspend.isPending ||
    resume.isPending ||
    saveName.isPending ||
    remove.isPending ||
    impersonate.isPending;

  return (
    <div className="space-y-4 p-6">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-3">
              {nameDraft === null ? (
                <>
                  {m.name}
                  <button
                    type="button"
                    className="text-xs font-normal text-muted-foreground hover:text-foreground"
                    onClick={() => setNameDraft(m.name)}
                  >
                    Modifica
                  </button>
                </>
              ) : (
                <form
                  className="flex items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    saveName.mutate(nameDraft);
                  }}
                >
                  <input
                    autoFocus
                    className="h-9 w-64 rounded-md border border-input bg-background px-3 text-sm"
                    value={nameDraft}
                    onChange={(e) => setNameDraft(e.target.value)}
                  />
                  <Button type="submit" size="sm" disabled={pending || nameDraft.trim() === ''}>
                    Salva
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setNameDraft(null)}
                    disabled={pending}
                  >
                    Annulla
                  </Button>
                </form>
              )}
            </CardTitle>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{m.slug}</p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={m.status} />
            <Button
              size="sm"
              onClick={() => {
                setMutationError(null);
                impersonate.mutate();
              }}
              disabled={pending}
            >
              Entra come merchant
            </Button>
            {isSuspended ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => resume.mutate()}
                disabled={pending}
              >
                Riattiva
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => suspend.mutate()}
                disabled={pending}
              >
                Sospendi
              </Button>
            )}
            <Button
              size="sm"
              variant="destructive"
              onClick={() => {
                setMutationError(null);
                setDeleteConfirm('');
              }}
              disabled={pending || deleteConfirm !== null}
            >
              Elimina
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm md:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">Timezone</dt>
              <dd>{m.timezone}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Locale</dt>
              <dd className="uppercase">{m.locale}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Tenant</dt>
              <dd className="font-mono text-xs">{m.tenant_id.slice(0, 8)}…</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Merchant ID</dt>
              <dd className="font-mono text-xs">{m.id.slice(0, 8)}…</dd>
            </div>
          </dl>
          {mutationError ? (
            <p className="mt-4 text-sm text-destructive">{mutationError}</p>
          ) : null}
          {deleteConfirm !== null ? (
            <div className="mt-4 rounded-md border border-destructive/40 bg-destructive/5 p-4">
              <p className="text-sm font-medium text-destructive">
                Eliminazione definitiva
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                Tutti i dati del merchant verranno rimossi in cascata: lead,
                conversazioni, knowledge base, configurazione bot, integrazioni e
                analytics. L&apos;azione non è reversibile.
              </p>
              <p className="mt-3 text-sm">
                Per confermare, digita lo slug{' '}
                <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                  {m.slug}
                </code>
                .
              </p>
              <form
                className="mt-3 flex items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (deleteConfirm === m.slug) remove.mutate();
                }}
              >
                <input
                  autoFocus
                  className="h-9 w-64 rounded-md border border-input bg-background px-3 font-mono text-sm"
                  value={deleteConfirm}
                  onChange={(e) => setDeleteConfirm(e.target.value)}
                  placeholder={m.slug}
                />
                <Button
                  type="submit"
                  size="sm"
                  variant="destructive"
                  disabled={pending || deleteConfirm !== m.slug}
                >
                  Elimina definitivamente
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setDeleteConfirm(null)}
                  disabled={pending}
                >
                  Annulla
                </Button>
              </form>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <MerchantKpiSection merchantId={merchantId} />

      <MerchantProfileSection merchantId={merchantId} />

      <InviteUserCard merchantId={merchantId} />
    </div>
  );
}

function MerchantKpiSection({ merchantId }: { merchantId: string }) {
  const query = useQuery({
    queryKey: ['merchant-kpis', merchantId],
    queryFn: async (): Promise<Kpis> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/analytics/merchant/kpis', {
        params: { query: { merchant_id: merchantId, since_days: 30 } },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Kpis;
    },
  });

  const k = query.data;
  const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <KPICard label="Lead totali" value={k ? k.leads_total : '—'} />
        <KPICard label="Lead hot" value={k ? k.leads_hot : '—'} />
        <KPICard label="Tasso risposta" value={k ? pct(k.response_rate) : '—'} />
        <KPICard label="Booking rate" value={k ? pct(k.booking_rate) : '—'} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Distribuzione score lead</CardTitle>
        </CardHeader>
        <CardContent>
          {query.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento KPI…</p>
          ) : query.isError ? (
            <p className="text-sm text-destructive">
              Errore caricamento KPI:{' '}
              {query.error instanceof Error ? query.error.message : 'sconosciuto'}
            </p>
          ) : !k || k.score_distribution.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessun dato ancora.</p>
          ) : (
            <div className="flex h-40 items-end gap-2">
              {k.score_distribution.map((b) => {
                const bucket = b.bucket ?? 0;
                const count = b.count ?? 0;
                return (
                  <div key={bucket} className="flex flex-1 flex-col items-center">
                    <div
                      className="w-full rounded-t bg-primary/70"
                      style={{
                        height: `${Math.min(100, count * 8)}%`,
                        minHeight: 4,
                      }}
                      title={`${bucket}-${bucket + 9}: ${count}`}
                    />
                    <span className="mt-1 text-xs text-muted-foreground">{bucket}</span>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ---- Profilo configurazione bot ----------------------------------------

const MERCHANT_SPECIFIC_KEYS = [
  { key: 'pipeline.default_pipeline_id', label: 'Pipeline ID (GHL)' },
  { key: 'pipeline.new_stage_id', label: 'New-lead stage ID (GHL)' },
  { key: 'pipeline.qualified_stage_id', label: 'Qualified stage ID (GHL)' },
  { key: 'booking.default_calendar_id', label: 'Calendar ID (GHL)' },
];

function MerchantProfileSection({ merchantId }: { merchantId: string }) {
  const queryClient = useQueryClient();
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  const [snapshotName, setSnapshotName] = useState('');
  const [snapshotDesc, setSnapshotDesc] = useState('');
  const [excludeKeys, setExcludeKeys] = useState<Set<string>>(
    new Set(MERCHANT_SPECIFIC_KEYS.map((k) => k.key)),
  );

  // Current overrides row — contains template_id
  const overridesQuery = useQuery({
    queryKey: ['merchant-overrides', merchantId],
    queryFn: async (): Promise<{ template_id: string | null }> => {
      return apiFetch<{ template_id: string | null }>(
        `/bot-config/${merchantId}/overrides`,
      );
    },
  });

  // All templates for the tenant
  const templatesQuery = useQuery({
    queryKey: ['templates', 'list'],
    queryFn: async (): Promise<Template[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/bot-config/templates');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Template[];
    },
  });

  const currentTemplateId = overridesQuery.data?.template_id ?? null;
  const currentTemplate = templatesQuery.data?.find((t) => t.id === currentTemplateId);

  const setTemplate = useMutation({
    mutationFn: async (templateId: string | null) => {
      return apiFetch(`/bot-config/${merchantId}/template`, {
        method: 'PUT',
        body: JSON.stringify({ template_id: templateId }),
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['merchant-overrides', merchantId] });
      toast.success('Profilo aggiornato');
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : 'Errore'),
  });

  const snapshot = useMutation({
    mutationFn: async () => {
      if (!snapshotName.trim()) throw new Error('Inserisci un nome');
      return apiFetch<Template>(`/bot-config/templates/from-merchant/${merchantId}`, {
        method: 'POST',
        body: JSON.stringify({
          name: snapshotName.trim(),
          description: snapshotDesc.trim() || null,
          exclude_keys: [...excludeKeys],
        }),
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['templates', 'list'] });
      toast.success('Profilo creato dalla configurazione del merchant');
      setSnapshotOpen(false);
      setSnapshotName('');
      setSnapshotDesc('');
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : 'Errore'),
  });

  function toggleExcludeKey(key: string) {
    setExcludeKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profilo configurazione bot</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Template corrente */}
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground min-w-24">Profilo attivo</span>
          {overridesQuery.isLoading ? (
            <span className="text-muted-foreground">Caricamento…</span>
          ) : (
            <span className="font-medium">
              {currentTemplate ? currentTemplate.name : 'Default di sistema / tenant'}
            </span>
          )}
        </div>

        {/* Dropdown cambio template */}
        <div className="flex items-center gap-3">
          <label className="text-sm text-muted-foreground min-w-24" htmlFor="tmpl-select">
            Cambia profilo
          </label>
          <select
            id="tmpl-select"
            className="h-9 rounded-md border border-input bg-background px-3 text-sm w-64"
            value={currentTemplateId ?? ''}
            disabled={setTemplate.isPending || templatesQuery.isLoading}
            onChange={(e) => {
              const val = e.target.value || null;
              setTemplate.mutate(val);
            }}
          >
            <option value="">Default di sistema / tenant</option>
            {(templatesQuery.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}{t.is_default ? ' (Default)' : ''}
              </option>
            ))}
          </select>
        </div>

        {/* Salva come profilo */}
        {snapshotOpen ? (
          <div className="rounded-md border p-4 space-y-3">
            <p className="text-sm font-medium">Salva configurazione come nuovo profilo</p>
            <p className="text-sm text-muted-foreground">
              Cattura la config risolta di questo merchant (override + cascata) come template riutilizzabile.
            </p>
            <div className="grid gap-2 md:grid-cols-2">
              <div className="space-y-1">
                <label className="text-xs font-medium" htmlFor="snap-name">Nome profilo</label>
                <input
                  id="snap-name"
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                  value={snapshotName}
                  onChange={(e) => setSnapshotName(e.target.value)}
                  placeholder="Es. Agenzia Immobiliare Standard"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium" htmlFor="snap-desc">Descrizione (opzionale)</label>
                <input
                  id="snap-desc"
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                  value={snapshotDesc}
                  onChange={(e) => setSnapshotDesc(e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-1">
              <p className="text-xs font-medium">Escludi chiavi merchant-specifiche</p>
              <p className="text-xs text-muted-foreground">
                Le chiavi escluse non saranno copiate nel profilo (consigliato per ID GHL specifici del merchant).
              </p>
              <div className="grid gap-1">
                {MERCHANT_SPECIFIC_KEYS.map((k) => (
                  <label key={k.key} className="flex items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={excludeKeys.has(k.key)}
                      onChange={() => toggleExcludeKey(k.key)}
                      className="h-3.5 w-3.5"
                    />
                    <span className="font-mono">{k.key}</span>
                    <span className="text-muted-foreground">{k.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => snapshot.mutate()}
                disabled={snapshot.isPending || !snapshotName.trim()}
              >
                {snapshot.isPending ? 'Salvataggio…' : 'Crea profilo'}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setSnapshotOpen(false)}
                disabled={snapshot.isPending}
              >
                Annulla
              </Button>
            </div>
          </div>
        ) : (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setSnapshotOpen(true)}
          >
            Salva come nuovo profilo…
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
