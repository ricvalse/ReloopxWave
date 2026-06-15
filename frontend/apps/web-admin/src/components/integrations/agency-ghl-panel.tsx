'use client';

import { useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { InstalledLocationsList } from './installed-locations-list';

type AgencyStatus = {
  connected: boolean;
  company_id: string | null;
  company_name: string | null;
  expires_at: number | null;
};

export function AgencyGhlPanel() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const justConnected =
    searchParams.get('provider') === 'ghl_agency' &&
    searchParams.get('status') === 'connected';

  const status = useQuery({
    queryKey: ['ghl', 'agency', 'status'],
    queryFn: async (): Promise<AgencyStatus> => {
      const api = getApiClient();
      const { data, error } = await api.GET(
        '/integrations/ghl/agency/status' as never,
        {} as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as AgencyStatus;
    },
  });

  const connect = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { data, error } = await api.POST(
        '/integrations/ghl/agency/oauth/start' as never,
        {} as never,
      );
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as { authorize_url: string };
    },
    onSuccess: (d) => {
      window.location.href = d.authorize_url;
    },
  });

  // GHL redirects the agency admin back here with
  // `?provider=ghl_agency&status=connected` after the callback persists the
  // agency token. Refresh the status + locations and clear the params.
  useEffect(() => {
    if (justConnected) {
      void queryClient.invalidateQueries({ queryKey: ['ghl', 'agency', 'status'] });
      void queryClient.invalidateQueries({ queryKey: ['ghl', 'locations'] });
      const url = new URL(window.location.href);
      url.searchParams.delete('provider');
      url.searchParams.delete('status');
      window.history.replaceState({}, '', url.pathname + url.search);
    }
  }, [justConnected, queryClient]);

  const connected = status.data?.connected ?? false;

  return (
    <div className="space-y-4 p-6">
      {justConnected ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
          Agenzia GoHighLevel collegata correttamente.
        </div>
      ) : null}

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>GoHighLevel — Agenzia</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Collega l&apos;agenzia una sola volta, poi installa l&apos;app sui sub-account
              dalla scheda Marketplace dell&apos;app. Le location installate compaiono qui
              sotto, pronte da associare ai merchant.
            </p>
          </div>
          <StatusPill connected={connected} />
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-4">
          <div className="text-sm text-muted-foreground">
            {status.isLoading ? (
              'Caricamento stato…'
            ) : connected ? (
              <>
                Company ID:{' '}
                <span className="font-mono text-xs">{status.data?.company_id}</span>
                {status.data?.company_name ? ` · ${status.data.company_name}` : ''}
              </>
            ) : (
              'Nessuna agenzia collegata.'
            )}
          </div>
          <div className="flex items-center gap-2">
            {connect.error ? (
              <span className="text-sm text-destructive">
                {connect.error instanceof Error ? connect.error.message : 'Errore'}
              </span>
            ) : null}
            <Button
              variant={connected ? 'outline' : 'default'}
              onClick={() => connect.mutate()}
              disabled={connect.isPending}
            >
              {connected ? 'Ricollega agenzia' : 'Collega agenzia GHL'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <InstalledLocationsList />
    </div>
  );
}

function StatusPill({ connected }: { connected: boolean }) {
  return (
    <span
      className={
        connected
          ? 'inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-900 ring-1 ring-inset ring-emerald-200'
          : 'inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground ring-1 ring-inset ring-border'
      }
    >
      {connected ? 'Connessa' : 'Non connessa'}
    </span>
  );
}
