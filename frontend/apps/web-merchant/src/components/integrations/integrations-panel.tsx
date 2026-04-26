'use client';

import { useSearchParams } from 'next/navigation';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

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
      // Redirect the current window — GHL will redirect back to the
      // callback → merchant portal, which invalidates the status query.
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
          {providerJustConnected === 'ghl' ? 'GHL' : providerJustConnected} connesso correttamente.
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
        onSubmit={(input) => verifyWA.mutate(input)}
        pending={verifyWA.isPending}
        error={verifyWA.error instanceof Error ? verifyWA.error.message : null}
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
  onSubmit,
  pending,
  error,
}: {
  connection: Connection | undefined;
  onSubmit: (input: { phone_number_id: string }) => void;
  pending: boolean;
  error: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [phoneNumberId, setPhoneNumberId] = useState('');
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
            Wave Marketing gestisce un unico Partner 360dialog: incolla il
            Phone Number ID del tuo canale e ti colleghi sotto la stessa
            console — l&apos;API key è già configurata a livello di piattaforma.
          </p>
        </div>
        <StatusPill connected={connected} label={connection?.status ?? 'disconnected'} />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-4">
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
          <Button
            variant={connected ? 'outline' : 'default'}
            onClick={() => setOpen((v) => !v)}
            disabled={pending}
          >
            {open ? 'Annulla' : connected ? 'Aggiorna' : 'Collega numero'}
          </Button>
        </div>
        {open ? (
          <form
            className="space-y-3 border-t pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              onSubmit({ phone_number_id: phoneNumberId });
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
                del canale del numero, lo stesso che Meta chiama
                phone_number_id.
              </p>
            </div>
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <div className="flex justify-end gap-2">
              <Button type="submit" disabled={pending || !phoneNumberId}>
                {pending ? 'Verifica…' : 'Verifica e salva'}
              </Button>
            </div>
          </form>
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
