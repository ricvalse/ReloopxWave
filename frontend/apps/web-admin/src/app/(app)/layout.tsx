import { AppShell } from '@reloop/ui';
import type { ReactNode } from 'react';
import { Sidebar } from '@/components/sidebar';
import { UserMenu } from '@/components/user-menu';
import { requireSession } from '@/server/require-session';

export default async function AppLayout({ children }: { children: ReactNode }) {
  const session = await requireSession();
  const email = session.user.email ?? '';
  const name = session.user.user_metadata?.full_name as string | undefined;

  return (
    <AppShell
      brand="Admin"
      user={{ email, name }}
      sidebar={<Sidebar />}
      userMenu={<UserMenu email={email} name={name} />}
    >
      {children}
    </AppShell>
  );
}
