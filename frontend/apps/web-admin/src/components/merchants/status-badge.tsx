import { cn } from '@reloop/ui';

const tone: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-900 ring-emerald-200',
  suspended: 'bg-red-100 text-red-900 ring-red-200',
  pending: 'bg-amber-100 text-amber-900 ring-amber-200',
};

export function StatusBadge({ status }: { status: string }) {
  const label =
    status === 'active' ? 'Attivo' : status === 'suspended' ? 'Sospeso' : status;
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        tone[status] ?? 'bg-muted text-muted-foreground ring-border',
      )}
    >
      {label}
    </span>
  );
}
