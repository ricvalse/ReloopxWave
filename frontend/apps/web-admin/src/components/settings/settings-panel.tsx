'use client';

import { useQuery } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import { Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getApiClient } from '@/lib/api';

type Tenant = components['schemas']['TenantOut'];

export function SettingsPanel() {
  const tenant = useQuery({
    queryKey: ['tenants', 'me'],
    queryFn: async (): Promise<Tenant> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/tenants/me' as never, {} as never);
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data as Tenant;
    },
  });

  return (
    <div className="space-y-4 p-6">
      <Card>
        <CardHeader>
          <CardTitle>Tenant</CardTitle>
        </CardHeader>
        <CardContent>
          {tenant.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento…</p>
          ) : tenant.isError ? (
            <p className="text-sm text-destructive">
              {tenant.error instanceof Error ? tenant.error.message : 'Errore'}
            </p>
          ) : tenant.data ? (
            <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm md:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Nome</dt>
                <dd>{tenant.data.name}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Slug</dt>
                <dd className="font-mono text-xs">{tenant.data.slug}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Stato</dt>
                <dd>{tenant.data.status}</dd>
              </div>
              <div className="col-span-full">
                <dt className="text-muted-foreground">Tenant ID</dt>
                <dd className="font-mono text-xs">{tenant.data.id}</dd>
              </div>
            </dl>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>In arrivo</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            Gestione team (invito utenti agenzia), webhook health, feature flags tenant-level:
            disponibili nelle prossime iterazioni.
          </p>
          <p>
            Per ora, le invite utente sono attivabili solo via chiamata API{' '}
            <code className="rounded bg-muted px-1 py-0.5 text-xs">POST /users/invite</code>.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
