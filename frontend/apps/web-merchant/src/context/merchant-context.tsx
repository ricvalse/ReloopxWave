'use client';

import { createContext, useContext } from 'react';

interface MerchantContextValue {
  merchantId: string | null;
  tenantId: string | null;
}

const MerchantContext = createContext<MerchantContextValue>({
  merchantId: null,
  tenantId: null,
});

export function MerchantProvider({
  merchantId,
  tenantId,
  children,
}: MerchantContextValue & { children: React.ReactNode }) {
  return (
    <MerchantContext.Provider value={{ merchantId, tenantId }}>
      {children}
    </MerchantContext.Provider>
  );
}

export function useMerchantContext() {
  return useContext(MerchantContext);
}
