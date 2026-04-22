'use client';

import { useEffect, useState } from 'react';
import { getBrowserSupabase } from '@/lib/supabase';

/** Reads the merchant_id custom JWT claim from the current Supabase session. */
export function useMerchantId(): { merchantId: string | null; tenantId: string | null } {
  const [merchantId, setMerchantId] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);

  useEffect(() => {
    const supabase = getBrowserSupabase();
    let active = true;
    (async () => {
      const { data } = await supabase.auth.getSession();
      const claims = data.session?.user?.app_metadata ?? {};
      if (!active) return;
      setMerchantId((claims as Record<string, unknown>).merchant_id as string ?? null);
      setTenantId((claims as Record<string, unknown>).tenant_id as string ?? null);
    })();
    return () => {
      active = false;
    };
  }, []);

  return { merchantId, tenantId };
}
