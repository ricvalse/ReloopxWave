import type { Route } from 'next';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { getServerSupabase } from '@/lib/supabase';
import {
  IMP_COOKIE,
  type MerchantSession,
  decodeJwtPayload,
  impSessionFromClaims,
  impTokenValid,
} from '@/lib/impersonation';

/**
 * Returns the active merchant session, normalized to {@link MerchantSession}.
 *
 * Two ways to be authenticated:
 * 1. A real Supabase session (cookies `sb-*`) — the merchant logging in.
 * 2. An agency impersonation session — a valid `imp-access-token` cookie.
 *    We shape its claims into the same minimal session the layout reads, so the
 *    rest of the app is identical whether the merchant or the agency is driving.
 */
export async function requireSession(): Promise<MerchantSession> {
  const cookieStore = await cookies();
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

  const impToken = cookieStore.get(IMP_COOKIE)?.value ?? null;
  const claims = impToken ? decodeJwtPayload(impToken) : null;
  if (impToken && impTokenValid(claims)) {
    return impSessionFromClaims(claims);
  }

  if (impToken) {
    // Cookie present but expired/invalid — bounce to the impersonation landing.
    redirect('/impersonation-expired' as Route);
  }
  redirect('/login');
}
