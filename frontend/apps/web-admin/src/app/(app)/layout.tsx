import { AppShell } from '@reloop/ui';
import type { ReactNode } from 'react';
import { Sidebar } from '@/components/sidebar';
import { requireSession } from '@/server/require-session';

export default async function AppLayout({ children }: { children: ReactNode }) {
  await requireSession();
  return <AppShell sidebar={<Sidebar />}>{children}</AppShell>;
}
