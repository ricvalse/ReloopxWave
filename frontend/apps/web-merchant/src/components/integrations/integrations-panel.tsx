'use client';

import { useSearchParams } from 'next/navigation';
import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { ConnectWhatsAppButton } from './connect-whatsapp-button';

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
      const { data, error } = await api.GET('/integrations/status' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Status;
    },
  });

  const startGHL = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { data, error } = await api.POST(
        '/integrations/ghl/oauth/start' as never,
        {} as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as { authorize_url: string };
    },
    onSuccess: (d) => {
      window.location.href = d.authorize_url;
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
    return <div className="p-6 text-sm text-muted-foreground">Caricamento stato…</div>;
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

  return (
    <div className="space-y-4 p-6">
      {providerJustConnected && connectionResult === 'connected' ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          {providerJustConnected === 'ghl' ? 'GHL' : 'WhatsApp'} connesso correttamente.
        </div>
      ) : null}

      <GhlCard
        connection={ghl}
        onConnect={() => startGHL.mutate()}
        pending={startGHL.isPending}
        error={startGHL.error instanceof Error ? startGHL.error.message : null}
      />

      <WhatsAppCard
        connection={wa}
        onPopupClosed={() =>
          queryClient.invalidateQueries({ queryKey: ['integrations', 'status'] })
        }
      />
    </div>
  );
}

function GhlCard({
  connection,
  onConnect,
  pending,
  error,
}: {
  connection: Connection | undefined;
  onConnect: () => void;
  pending: boolean;
  error: string | null;
}) {
  const connected = connection?.connected ?? false;
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>GoHighLevel</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            CRM, pipeline opportunità, calendario. Connettiti via OAuth.
          </p>
        </div>
        <StatusPill connected={connected} label={connection?.status ?? 'disconnected'} />
      </CardHeader>
      <CardContent className="flex items-center justify-between gap-4">
        <div className="text-sm text-muted-foreground">
          {connected ? (
            <>
              Location:{' '}
              <span className="font-mono text-xs">
                {connection?.external_account_id ?? '—'}
              </span>
            </>
          ) : (
            'Nessuna location collegata.'
          )}
        </div>
        <div className="flex items-center gap-2">
          {error ? <span className="text-sm text-destructive">{error}</span> : null}
          <Button
            variant={connected ? 'outline' : 'default'}
            onClick={onConnect}
            disabled={pending}
          >
            {connected ? 'Riconnetti' : 'Connetti GHL'}
          </Button>
        </div>
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
