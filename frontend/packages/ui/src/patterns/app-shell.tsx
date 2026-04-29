import type { ReactNode } from 'react';

// AppShell is now in shell/app-shell.tsx — re-exported here for backward compatibility.
export { AppShell } from '../shell/app-shell';
export type { AppShellProps } from '../shell/app-shell';

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
    <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
      <div>
        <h1 className="text-xl font-semibold leading-tight tracking-tight">{title}</h1>
        {description ? <p className="mt-0.5 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions}
    </div>
  );
}
