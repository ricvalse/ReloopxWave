'use client';

import { useEffect, useState } from 'react';
import { getBrowserSupabase } from '@/lib/supabase';
import {
  IMP_META_COOKIE,
  type ImpMeta,
  clearImpCookiesBrowser,
  readCookieBrowser,
} from '@/lib/impersonation';

function formatRemaining(seconds: number): string {
  if (seconds <= 0) return '00:00';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/**
 * Persistent banner shown while an agency admin impersonates a merchant.
 * Reads the `imp-meta` cookie for the merchant name + expiry, counts down, and
 * lets the admin leave. When the window elapses it flips to an expired state.
 */
export function ImpersonationBanner() {
  const [meta, setMeta] = useState<ImpMeta | null>(null);
  const [remaining, setRemaining] = useState<number>(0);

  useEffect(() => {
    const raw = readCookieBrowser(IMP_META_COOKIE);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as ImpMeta;
      setMeta(parsed);
      setRemaining(Math.max(0, parsed.expiresAt - Math.floor(Date.now() / 1000)));
    } catch {
      setMeta(null);
    }
  }, []);

  useEffect(() => {
    if (!meta) return;
    const id = setInterval(() => {
      setRemaining(Math.max(0, meta.expiresAt - Math.floor(Date.now() / 1000)));
    }, 1000);
    return () => clearInterval(id);
  }, [meta]);

  if (!meta) return null;

  const expired = remaining <= 0;

  const onExit = () => {
    clearImpCookiesBrowser();
    // Revert the Realtime socket to the anon/session token so it isn't left
    // authorized as the now-ended merchant (belt-and-suspenders with the gate).
    void getBrowserSupabase().realtime.setAuth();
    if (window.opener) {
      window.close();
    } else {
      window.location.href = '/impersonation-expired';
    }
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-300 bg-amber-100 px-4 py-2 text-sm text-amber-950">
      <span className="flex items-center gap-2">
        <span aria-hidden>🛡️</span>
        {expired ? (
          <span className="font-medium">
            Sessione di impersonazione di <strong>{meta.merchantName}</strong> scaduta.
          </span>
        ) : (
          <span>
            Stai configurando come <strong>{meta.merchantName}</strong> (sessione agenzia) —
            scade tra <span className="font-mono">{formatRemaining(remaining)}</span>.
          </span>
        )}
      </span>
      <button
        type="button"
        onClick={onExit}
        className="rounded-md border border-amber-400 bg-amber-50 px-2 py-1 text-xs font-medium hover:bg-amber-200"
      >
        Esci dall&apos;impersonazione
      </button>
    </div>
  );
}
