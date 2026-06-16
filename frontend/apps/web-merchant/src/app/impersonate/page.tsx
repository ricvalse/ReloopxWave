'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  IMP_COOKIE,
  IMP_META_COOKIE,
  type ImpMeta,
  decodeJwtPayload,
  impTokenValid,
} from '@/lib/impersonation';

/**
 * Cross-domain handoff landing for agency impersonation.
 *
 * web-admin opens `…/impersonate#token=<jwt>&exp=<epoch>`. The token rides the
 * URL *fragment* (never sent to the server, never in access logs). Here we read
 * it, persist it in the short-lived `imp-access-token` cookie, wipe the hash,
 * and bounce to the dashboard — from which the whole merchant portal runs as
 * that merchant.
 */
export default function ImpersonatePage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const raw = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : '';
    // Strip the token from history immediately.
    window.history.replaceState(null, '', window.location.pathname);

    const params = new URLSearchParams(raw);
    const token = params.get('token');
    if (!token) {
      setError('Token di impersonazione mancante.');
      return;
    }

    const claims = decodeJwtPayload(token);
    if (!impTokenValid(claims)) {
      setError('Token di impersonazione non valido o scaduto.');
      return;
    }

    const expiresAt = claims.exp as number;
    const maxAge = Math.max(1, expiresAt - Math.floor(Date.now() / 1000));
    const meta: ImpMeta = {
      merchantName:
        (claims.user_metadata?.impersonated_merchant_name as string | undefined) ?? 'merchant',
      expiresAt,
      sessionId: claims.session_id ?? '',
    };

    const secure = window.location.protocol === 'https:' ? '; secure' : '';
    const base = `path=/; max-age=${maxAge}; samesite=lax${secure}`;
    document.cookie = `${IMP_COOKIE}=${encodeURIComponent(token)}; ${base}`;
    document.cookie = `${IMP_META_COOKIE}=${encodeURIComponent(JSON.stringify(meta))}; ${base}`;

    router.replace('/dashboard');
  }, [router]);

  return (
    <div className="flex min-h-screen items-center justify-center p-6 text-sm">
      {error ? (
        <div className="max-w-md text-center">
          <p className="font-medium text-destructive">{error}</p>
          <p className="mt-2 text-muted-foreground">
            Torna al pannello agenzia e riprova ad accedere come merchant.
          </p>
        </div>
      ) : (
        <p className="text-muted-foreground">Avvio sessione di impersonazione…</p>
      )}
    </div>
  );
}
