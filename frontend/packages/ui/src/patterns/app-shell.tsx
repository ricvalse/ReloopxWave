import type { ReactNode } from 'react';
import { cn } from '../utils';

export type AppShellProps = {
  sidebar: ReactNode;
  children: ReactNode;
  className?: string;
};

export function AppShell({ sidebar, children, className }: AppShellProps) {
  return (
    <div className={cn('flex min-h-screen', className)}>
      <aside className="hidden w-64 border-r bg-muted/40 md:flex md:flex-col">{sidebar}</aside>
      <main className="flex flex-1 flex-col">{children}</main>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b px-6 py-4">
      <div>
        <h1 className="text-xl font-semibold leading-tight">{title}</h1>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions}
    </div>
  );
}
