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

  const status = useQuery({
    queryKey: ['integrations', 'status'],
    queryFn: async (): Promise<Status> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/integrations/status');
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Status;
    },
  });

  // The router's `/onboard/callback` redirects the browser back here with
  // `?provider=whatsapp&status=connected` after persisting the channel via
  // `/internal/whatsapp-connected`. Just refresh the status query and clear
  // the params so a reload doesn't re-show the banner.
  useEffect(() => {
    if (
      providerJustConnected === 'whatsapp' &&
      connectionResult === 'connected'
    ) {
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'status'] });
      const url = new URL(window.location.href);
      url.searchParams.delete('provider');
      url.searchParams.delete('status');
      window.history.replaceState({}, '', url.pathname + url.search);
    }
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
      {providerJustConnected && connectionResult === 'connected' ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          {providerJustConnected === 'ghl' ? 'GHL' : 'WhatsApp'} connesso correttamente.
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
  const [webhook, setWebhook] = useState('');
  const [error, setError] = useState<string | null>(null);

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
            Ricevi un avviso su Slack quando una conversazione passa a un operatore. Crea un
            «Incoming Webhook» su Slack, incolla qui l&apos;URL, poi aggiungi il nodo «Notifica
            Slack» alle tue automazioni.
          </p>
        </div>
        <StatusPill connected={connected} label={connection?.status ?? 'disconnected'} />
      </CardHeader>
      <CardContent className="space-y-3">
        {connected ? (
          <div className="flex items-center justify-between gap-4 text-sm text-muted-foreground">
            <span>Webhook configurato.</span>
            <Button
              variant="outline"
              size="sm"
              disabled={disconnect.isPending}
              onClick={() => disconnect.mutate()}
            >
              Scollega
            </Button>
          </div>
        ) : null}
        <div className="flex items-start gap-2">
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
            {connected ? 'Aggiorna' : 'Salva'}
          </Button>
        </div>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        {webhook && !canSave ? (
          <p className="text-xs text-muted-foreground">
            L&apos;URL deve iniziare con https://hooks.slack.com/
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
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
