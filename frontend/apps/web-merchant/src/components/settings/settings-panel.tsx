'use client';

import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getBrowserSupabase } from '@/lib/supabase';

export function SettingsPanel() {
  const session = useQuery({
    queryKey: ['auth', 'session'],
    queryFn: async () => {
      const supabase = getBrowserSupabase();
      const { data } = await supabase.auth.getSession();
      return data.session;
    },
  });

  const user = session.data?.user;
  const claims = (user?.app_metadata as Record<string, unknown> | undefined) ?? {};

  return (
    <div className="space-y-4 p-6">
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent>
          {!user ? (
            <p className="text-sm text-muted-foreground">Sessione non trovata.</p>
          ) : (
            <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm md:grid-cols-3">
              <div>
                <dt className="text-muted-foreground">Email</dt>
                <dd>{user.email ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Ruolo</dt>
                <dd>{String(claims.role ?? '—')}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Merchant ID</dt>
                <dd className="font-mono text-xs">
                  {String(claims.merchant_id ?? '—')}
                </dd>
              </div>
              <div className="col-span-full">
                <dt className="text-muted-foreground">User ID</dt>
                <dd className="font-mono text-xs">{user.id}</dd>
              </div>
            </dl>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>In arrivo</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>Notifiche, timezone, export dati: disponibili nelle prossime iterazioni.</p>
          <p>
            Per richiedere un export CSV di eventi analytics, contatta l&apos;agenzia — il flusso
            self-service è pianificato come follow-up.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
