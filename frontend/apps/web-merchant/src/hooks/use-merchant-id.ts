'use client';

import { useMerchantContext } from '@/context/merchant-context';

/** Reads merchant_id and tenant_id from the server-injected MerchantContext (no async waterfall). */
export function useMerchantId() {
  return useMerchantContext();
}
