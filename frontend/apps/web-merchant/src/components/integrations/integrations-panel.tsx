'use client';

import { useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle, Input, SkeletonCard } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { ConnectWhatsAppButton } from './connect-whatsapp-button';
import { GhlSyncLog } from './ghl-sync-log';

type Status = components['schemas']['StatusOut'];
type Connection = components['schemas']['ConnectionOut'];

export function IntegrationsPanel() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const providerJustConnected = searchParams.get('provider');
  const connectionResult = searchParams.get('status');

  // Banner state is captured once from the URL, NOT read live from searchParams:
  // clearing the params below calls replaceState, which in the App Router
  // re-syncs useSearchParams and would otherwise unmount the banner after a frame
  // (Slack is a full-page redirect, so the banner lands in the main window).
  const [banner, setBanner] = useState<{ provider: string; status: string } | null>(null);

  const status = useQuery({
    queryKey: ['integrations', 'status'],
    queryFn: async (): Promise<Status> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/integrations/status');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Status;
    },
  });

  // An OAuth/onboarding round-trip (WhatsApp `/onboard/callback`, Slack
  // `/slack/oauth/callback`) redirects the browser back here with
  // `?provider=<p>&status=connected|error`. Capture it for the banner, refresh
  // the status query, then clear the params so a reload doesn't re-show it.
  useEffect(() => {
    if (!providerJustConnected || !connectionResult) return;
    setBanner({ provider: providerJustConnected, status: connectionResult });
    if (connectionResult === 'connected') {
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'status'] });
    }
    const url = new URL(window.location.href);
    url.searchParams.delete('provider');
    url.searchParams.delete('status');
    window.history.replaceState({}, '', url.pathname + url.search);
  }, [providerJustConnected, connectionResult, queryClient]);

  if (status.isLoading) {
    return (
      <div className="grid gap-4 p-6 md:grid-cols-2">
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );
  }

  if (status.isError) {
    return (
      <div className="p-6 text-sm text-destructive">
        Errore caricamento stato:{' '}
        {status.error instanceof Error ? status.error.message : 'sconosciuto'}
      </div>
    );
  }

  const ghl = status.data?.connections.find((c) => c.provider === 'ghl');
  const wa = status.data?.connections.find((c) => c.provider === 'whatsapp');
  const slack = status.data?.connections.find((c) => c.provider === 'slack');

  return (
    <div className="space-y-4 p-6">
      {banner?.status === 'connected' ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          {providerLabel(banner.provider)} connesso correttamente.
        </div>
      ) : banner?.provider === 'slack' && banner?.status === 'error' ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          Collegamento Slack non riuscito. Riprova.
        </div>
      ) : null}

      <GhlCard connection={ghl} />

      <WhatsAppCard
        connection={wa}
        onPopupClosed={() =>
          queryClient.invalidateQueries({ queryKey: ['integrations', 'status'] })
        }
      />

      <SlackCard
        connection={slack}
        onChanged={() =>
          queryClient.invalidateQueries({ queryKey: ['integrations', 'status'] })
        }
      />

      {ghl?.connected ? <GhlSyncLog /> : null}
    </div>
  );
}

function GhlCard({ connection }: { connection: Connection | undefined }) {
  // GHL is agency-managed (marketplace install): the agency connects GoHighLevel
  // and links this merchant's location from the admin portal. The merchant sees
  // a read-only status, not a self-service connect button.
  const connected = connection?.connected ?? false;
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>GoHighLevel</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            CRM, pipeline opportunità, calendario. Gestito dalla tua agenzia.
          </p>
        </div>
        <StatusPill connected={connected} label={connection?.status ?? 'disconnected'} />
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        {connected ? (
          <>
            Collegato tramite agenzia · Location:{' '}
            <span className="font-mono text-xs">
              {connection?.external_account_id ?? '—'}
            </span>
          </>
        ) : (
          'Gestito dall’agenzia — nessuna location ancora collegata. Contatta la tua agenzia per attivare il collegamento.'
        )}
      </CardContent>
    </Card>
  );
}

function WhatsAppCard({
  connection,
  onPopupClosed,
}: {
  connection: Connection | undefined;
  onPopupClosed: () => void;
}) {
  const connected = connection?.connected ?? false;
  const displayPhone =
    (typeof connection?.meta?.display_phone === 'string' && connection.meta.display_phone) ||
    null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>WhatsApp (360dialog)</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Collega il tuo numero WhatsApp: ti apriamo la procedura ufficiale
            di Meta in una finestra separata, completi l&apos;iscrizione e
            torni qui — il canale sarà già attivo.
          </p>
        </div>
        <StatusPill connected={connected} label={connection?.status ?? 'disconnected'} />
      </CardHeader>
      <CardContent>
        <div className="flex items-start justify-between gap-4">
          <div className="text-sm text-muted-foreground">
            {connected ? (
              <>
                Phone:{' '}
                <span className="font-mono text-xs">
                  {connection?.external_account_id ?? '—'}
                </span>
                {displayPhone ? ` (${displayPhone})` : null}
              </>
            ) : (
              'Nessun numero collegato.'
            )}
          </div>
          <ConnectWhatsAppButton
            onPopupClosed={onPopupClosed}
            label={connected ? 'Sostituisci canale' : undefined}
            reconnect={connected}
            onDisconnected={onPopupClosed}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function SlackCard({
  connection,
  onChanged,
}: {
  connection: Connection | undefined;
  onChanged: () => void;
}) {
  const connected = connection?.connected ?? false;
  const channel =
    typeof connection?.meta?.channel === 'string' ? connection.meta.channel : null;
  const [webhook, setWebhook] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  // "Add to Slack": fetch the authorize URL and hand the browser to Slack. Slack
  // asks which channel to post to and returns a ready webhook — no copy-paste.
  const startOAuth = async () => {
    setStarting(true);
    setError(null);
    try {
      const api = getApiClient();
      const { data, error: err } = await api.GET('/integrations/slack/oauth/start');
      if (err || !data) {
        throw new Error(typeof err === 'string' ? err : "Impossibile avviare l'installazione");
      }
      window.location.href = data.authorize_url;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Errore avvio installazione');
      setStarting(false);
    }
  };

  const save = useMutation({
    mutationFn: async (url: string) => {
      const api = getApiClient();
      const { error: err } = await api.POST('/integrations/slack', {
        body: { webhook_url: url },
      });
      if (err) throw new Error(typeof err === 'string' ? err : JSON.stringify(err));
    },
    onSuccess: () => {
      setWebhook('');
      setError(null);
      onChanged();
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Errore salvataggio'),
  });

  const disconnect = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { error: err } = await api.POST('/integrations/slack/disconnect');
      if (err) throw new Error(typeof err === 'string' ? err : JSON.stringify(err));
    },
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Errore disconnessione'),
  });

  // The webhook is a secret; a Slack incoming webhook always lives under this host.
  const canSave = webhook.trim().startsWith('https://hooks.slack.com/');

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Slack</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Ricevi un avviso su Slack quando una conversazione passa a un operatore. Un click,
            scegli il canale, e la notifica dell&apos;handoff è già attiva.
          </p>
        </div>
        <StatusPill connected={connected} label={connection?.status ?? 'disconnected'} />
      </CardHeader>
      <CardContent className="space-y-3">
        {connected ? (
          <div className="flex items-center justify-between gap-4 text-sm text-muted-foreground">
            <span>Connesso{channel ? ` · ${channel}` : ''}.</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={starting} onClick={startOAuth}>
                Riconnetti
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={disconnect.isPending}
                onClick={() => disconnect.mutate()}
              >
                Scollega
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" disabled={starting} onClick={startOAuth}>
            {starting ? 'Reindirizzamento…' : 'Aggiungi a Slack'}
          </Button>
        )}
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">
            Avanzato: incolla un webhook manualmente
          </summary>
          <div className="mt-2 flex items-start gap-2">
            <Input
              type="url"
              placeholder="https://hooks.slack.com/services/…"
              value={webhook}
              onChange={(e) => {
                setWebhook(e.target.value);
                setError(null);
              }}
            />
            <Button
              size="sm"
              disabled={!canSave || save.isPending}
              onClick={() => save.mutate(webhook.trim())}
            >
              Salva
            </Button>
          </div>
          {webhook && !canSave ? (
            <p className="mt-1">L&apos;URL deve iniziare con https://hooks.slack.com/</p>
          ) : null}
        </details>
      </CardContent>
    </Card>
  );
}

function providerLabel(provider: string): string {
  if (provider === 'ghl') return 'GHL';
  if (provider === 'slack') return 'Slack';
  return 'WhatsApp';
}

function StatusPill({ connected, label }: { connected: boolean; label: string }) {
  return (
    <span
      className={
        connected
          ? 'inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-900 ring-1 ring-inset ring-emerald-200'
          : 'inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground ring-1 ring-inset ring-border'
      }
    >
      {connected ? 'Connesso' : label}
    </span>
  );
}
