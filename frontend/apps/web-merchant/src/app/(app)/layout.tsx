import { AppShell } from '@reloop/ui';
import type { ReactNode } from 'react';
import { Sidebar } from '@/components/sidebar';
import { UserMenu } from '@/components/user-menu';
import { MerchantProvider } from '@/context/merchant-context';
import { requireSession } from '@/server/require-session';

export default async function AppLayout({ children }: { children: ReactNode }) {
  const session = await requireSession();
  const email = session.user.email ?? '';
  const name = session.user.user_metadata?.full_name as string | undefined;
  const claims = (session.user.app_metadata ?? {}) as Record<string, unknown>;
  const merchantId = (claims['merchant_id'] as string | undefined) ?? null;
  const tenantId = (claims['tenant_id'] as string | undefined) ?? null;

  return (
    <MerchantProvider merchantId={merchantId} tenantId={tenantId}>
      <AppShell
        brand="Merchant"
        user={{ email, name }}
        sidebar={<Sidebar />}
        userMenu={<UserMenu email={email} name={name} />}
      >
        {children}
      </AppShell>
    </MerchantProvider>
  );
}
