import type { ReactNode } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../primitives/card';
import { cn } from '../utils';

export type KPICardProps = {
  label: string;
  value: ReactNode;
  delta?: { value: number; label: string } | null;
  icon?: ReactNode;
  className?: string;
};

export function KPICard({ label, value, delta, icon, className }: KPICardProps) {
  return (
    <Card className={cn('flex-1', className)}>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
        {icon ? <span className="text-muted-foreground">{icon}</span> : null}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {delta ? (
          <p
            className={cn(
              'text-xs',
              delta.value >= 0 ? 'text-emerald-600' : 'text-destructive',
            )}
          >
            {delta.value >= 0 ? '+' : ''}
            {delta.value}% {delta.label}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
