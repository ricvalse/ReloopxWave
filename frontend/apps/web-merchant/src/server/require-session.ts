import type { Route } from 'next';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { getServerSupabase } from '@/lib/supabase';
import {
  IMP_COOKIE,
  type MerchantSession,
  activeImpersonationToken,
  decodeJwtPayload,
  impSessionFromClaims,
} from '@/lib/impersonation';

/**
 * Returns the active merchant session, normalized to {@link MerchantSession}.
 *
 * Two ways to be authenticated:
 * 1. An agency impersonation session — a valid `imp-access-token` cookie.
 * 2. A real Supabase session (cookies `sb-*`) — the merchant logging in.
 *
 * Impersonation is checked FIRST — see `activeImpersonationToken` for why: it
 * must win over a stale real session in the same browser, or this function
 * would hand the layout a different merchant_id than the one `lib/api.ts` (and
 * every other client-side consumer) puts in the Authorization header.
 */
export async function requireSession(): Promise<MerchantSession> {
  const cookieStore = await cookies();

  const impToken = activeImpersonationToken(cookieStore.get(IMP_COOKIE)?.value ?? null);
  if (impToken) {
    return impSessionFromClaims(decodeJwtPayload(impToken)!);
  }

  const supabase = getServerSupabase({
    getAll: () => cookieStore.getAll(),
    setAll: (pairs) => {
      for (const { name, value, options } of pairs) {
        cookieStore.set({ name, value, ...options });
      }
    },
  });

  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (session) {
    return {
      user: {
        email: session.user.email,
        app_metadata: session.user.app_metadata as Record<string, unknown>,
        user_metadata: session.user.user_metadata as Record<string, unknown>,
      },
      isImpersonation: false,
    };
  }

  if (cookieStore.get(IMP_COOKIE)?.value) {
    // Cookie present but expired/invalid — bounce to the impersonation landing.
    redirect('/impersonation-expired' as Route);
  }
  redirect('/login');
}
