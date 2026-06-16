'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { getBrowserSupabase } from '@/lib/supabase';
import {
  IMP_COOKIE,
  IMP_META_COOKIE,
  type ImpMeta,
  clearImpCookiesBrowser,
  decodeJwtPayload,
  impTokenValid,
  readCookieBrowser,
} from '@/lib/impersonation';

type Account = {
  email: string | null;
  role: string;
  merchantId: string;
  userId: string;
  isImpersonation: boolean;
  merchantName?: string;
};

export function SettingsPanel() {
  const router = useRouter();
  const [signingOut, setSigningOut] = useState(false);

  // Normalize both auth paths into one Account: a real Supabase session, or an
  // agency impersonation session (token in the imp cookie, no supabase-js
  // session). Runs only on the client, so the cookie reads are safe.
  const account = useQuery({
    queryKey: ['auth', 'account'],
    queryFn: async (): Promise<Account | null> => {
      const impToken = readCookieBrowser(IMP_COOKIE);
      const impClaims = impToken ? decodeJwtPayload(impToken) : null;
      if (impToken && impTokenValid(impClaims)) {
        const md = (impClaims.app_metadata ?? {}) as Record<string, unknown>;
        let merchantName: string | undefined;
        const rawMeta = readCookieBrowser(IMP_META_COOKIE);
        if (rawMeta) {
          try {
            merchantName = (JSON.parse(rawMeta) as ImpMeta).merchantName;
          } catch {
            /* ignore malformed meta cookie */
          }
        }
        return {
          email: impClaims.act?.email ?? null,
          role: String(md.role ?? impClaims.user_role ?? '—'),
          merchantId: String(md.merchant_id ?? impClaims.merchant_id ?? '—'),
          userId: String(impClaims.sub ?? '—'),
          isImpersonation: true,
          merchantName,
        };
      }
      const supabase = getBrowserSupabase();
      const { data } = await supabase.auth.getSession();
      const u = data.session?.user;
      if (!u) return null;
      const claims = (u.app_metadata as Record<string, unknown> | undefined) ?? {};
      return {
        email: u.email ?? null,
        role: String(claims.role ?? '—'),
        merchantId: String(claims.merchant_id ?? '—'),
        userId: u.id,
        isImpersonation: false,
      };
    },
  });

  const acc = account.data ?? null;
  const isImpersonation = acc?.isImpersonation ?? false;

  const onSignOut = async () => {
    setSigningOut(true);
    if (isImpersonation) {
      // End the impersonation session (same flow as the banner's "Esci").
      clearImpCookiesBrowser();
      if (typeof window !== 'undefined' && window.opener) {
        window.close();
      } else {
        router.replace('/impersonation-expired');
      }
      return;
    }
    await getBrowserSupabase().auth.signOut();
    router.replace('/login');
  };

  return (
    <div className="space-y-4 p-6">
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent>
          {account.isLoading ? (
            <p className="text-sm text-muted-foreground">Caricamento…</p>
          ) : !acc ? (
            <p className="text-sm text-muted-foreground">Sessione non trovata.</p>
          ) : (
            <>
              {isImpersonation ? (
                <p className="mb-3 inline-flex items-center gap-2 rounded-md bg-amber-100 px-2 py-1 text-xs font-medium text-amber-950 ring-1 ring-inset ring-amber-200">
                  🛡️ Sessione agenzia
                  {acc.merchantName ? <> — impersonazione di {acc.merchantName}</> : null}
                </p>
              ) : null}
              <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm md:grid-cols-3">
                <div>
                  <dt className="text-muted-foreground">{isImpersonation ? 'Agenzia (email)' : 'Email'}</dt>
                  <dd>{acc.email ?? '—'}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Ruolo</dt>
                  <dd>{acc.role}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Merchant ID</dt>
                  <dd className="font-mono text-xs">{acc.merchantId}</dd>
                </div>
                <div className="col-span-full">
                  <dt className="text-muted-foreground">User ID</dt>
                  <dd className="font-mono text-xs">{acc.userId}</dd>
                </div>
              </dl>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sessione</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-4">
          <p className="text-sm text-muted-foreground">
            {isImpersonation
              ? 'Termina la sessione di impersonazione e torna all’agenzia.'
              : 'Esci da questo dispositivo. Dovrai inserire di nuovo email e password per rientrare.'}
          </p>
          <Button variant="outline" onClick={onSignOut} disabled={signingOut}>
            {signingOut
              ? 'Uscita…'
              : isImpersonation
                ? 'Esci dall’impersonazione'
                : 'Esci'}
          </Button>
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
