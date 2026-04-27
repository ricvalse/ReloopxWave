'use client';

import { useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { parseChannels } from '@/lib/whatsapp/parse-channels';
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

  const verifyWA = useMutation({
    mutationFn: async (input: { phone_number_id: string }) => {
      const api = getApiClient();
      const { data, error } = await api.POST('/integrations/whatsapp/verify' as never, {
        body: input,
      } as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Connection;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'status'] });
    },
  });

  const provisionChannel = useMutation({
    mutationFn: async (input: { channel_id: string; phone_number?: string }) => {
      const api = getApiClient();
      const { data, error } = await api.POST(
        '/integrations/whatsapp/channels' as never,
        { body: input } as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Connection;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['integrations', 'status'] });
    },
  });

  // 360dialog Embedded Signup redirect handler.
  //
  // On success the popup closes and 360dialog redirects the parent to
  // `/integrations?client=<phone>&channels=[<channel_id>]`. Pick those off
  // the URL exactly once, POST them to the provisioning route, then strip
  // the params so a refresh doesn't re-fire.
  const fired = useRef(false);
  useEffect(() => {
    if (fired.current) return;
    if (provisionChannel.isPending || provisionChannel.isSuccess) return;
    const channels = parseChannels(searchParams.get('channels'));
    const channelId = channels[0];
    const phoneNumber = searchParams.get('client') || undefined;
    if (!channelId) return;
    fired.current = true;
    provisionChannel.mutate(
      { channel_id: channelId, phone_number: phoneNumber },
      {
        onSettled: () => {
          // Clear the 360dialog redirect params either way so the user
          // doesn't see them in the address bar after the call resolves.
          // window.history avoids Next's typed-route strictness for what
          // is just a same-page query-param scrub.
          const url = new URL(window.location.href);
          url.searchParams.delete('channels');
          url.searchParams.delete('client');
          url.searchParams.delete('revoked');
          window.history.replaceState({}, '', url.pathname + url.search);
        },
      },
    );
  }, [searchParams, provisionChannel]);

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
  const merchantId = status.data?.merchant_id ?? '';

  return (
    <div className="space-y-4 p-6">
      {providerJustConnected && connectionResult === 'connected' ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          {providerJustConnected === 'ghl' ? 'GHL' : providerJustConnected} connesso correttamente.
        </div>
      ) : null}

      {provisionChannel.isPending ? (
        <div className="rounded-md border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
          Sto creando il canale WhatsApp con 360dialog…
        </div>
      ) : null}
      {provisionChannel.isError ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          Errore durante la creazione del canale:{' '}
          {provisionChannel.error instanceof Error
            ? provisionChannel.error.message
            : 'sconosciuto'}
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
        merchantId={merchantId}
        onManualSubmit={(input) => verifyWA.mutate(input)}
        manualPending={verifyWA.isPending}
        manualError={verifyWA.error instanceof Error ? verifyWA.error.message : null}
        provisionPending={provisionChannel.isPending}
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
  merchantId,
  onManualSubmit,
  manualPending,
  manualError,
  provisionPending,
}: {
  connection: Connection | undefined;
  merchantId: string;
  onManualSubmit: (input: { phone_number_id: string }) => void;
  manualPending: boolean;
  manualError: string | null;
  provisionPending: boolean;
}) {
  const [manualOpen, setManualOpen] = useState(false);
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const connected = connection?.connected ?? false;
  const displayPhone =
    (typeof connection?.meta?.display_phone === 'string' && connection.meta.display_phone) ||
    null;
  const createdVia =
    typeof connection?.meta?.created_via === 'string' ? connection.meta.created_via : null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>WhatsApp (360dialog)</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Collega il tuo numero WhatsApp via 360dialog: ti apriamo la
            procedura ufficiale di Meta in una finestra separata, completi
            l&apos;iscrizione e torni qui — il canale sarà già attivo.
          </p>
        </div>
        <StatusPill connected={connected} label={connection?.status ?? 'disconnected'} />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="text-sm text-muted-foreground">
            {connected ? (
              <>
                Phone:{' '}
                <span className="font-mono text-xs">
                  {connection?.external_account_id ?? '—'}
                </span>
                {displayPhone ? ` (${displayPhone})` : null}
                {createdVia ? (
                  <span className="ml-2 text-xs">
                    {createdVia === 'partner_hub' ? '· canale autonomo' : '· inserito manualmente'}
                  </span>
                ) : null}
              </>
            ) : (
              'Nessun numero collegato.'
            )}
          </div>
          {merchantId ? (
            <ConnectWhatsAppButton
              merchantId={merchantId}
              pending={provisionPending}
              label={connected ? 'Sostituisci canale' : undefined}
            />
          ) : null}
        </div>

        <div className="border-t pt-3">
          <button
            type="button"
            onClick={() => setManualOpen((v) => !v)}
            className="text-xs text-muted-foreground underline-offset-4 hover:underline"
          >
            {manualOpen
              ? 'Nascondi inserimento manuale'
              : 'Hai già un canale? Inseriscilo manualmente'}
          </button>
          {manualOpen ? (
            <form
              className="mt-3 space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                onManualSubmit({ phone_number_id: phoneNumberId });
              }}
            >
              <div className="space-y-1">
                <label className="text-sm font-medium" htmlFor="phone-number-id">
                  Phone Number ID
                </label>
                <input
                  id="phone-number-id"
                  required
                  value={phoneNumberId}
                  onChange={(e) => setPhoneNumberId(e.target.value)}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  Lo trovi nel Partner Hub di 360dialog — è l&apos;identificativo
                  del canale (lo stesso che Meta chiama phone_number_id).
                  Salta questo passaggio se hai usato il pulsante qui sopra.
                </p>
              </div>
              {manualError ? <p className="text-sm text-destructive">{manualError}</p> : null}
              <div className="flex justify-end gap-2">
                <Button type="submit" disabled={manualPending || !phoneNumberId}>
                  {manualPending ? 'Verifica…' : 'Verifica e salva'}
                </Button>
              </div>
            </form>
          ) : null}
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
