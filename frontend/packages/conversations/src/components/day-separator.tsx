import { formatDaySeparator } from '../lib/time';

export function DaySeparator({ iso }: { iso: string }) {
  return (
    <div className="my-4 flex items-center justify-center">
      <span className="rounded-full border border-border bg-card px-3 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {formatDaySeparator(iso)}
      </span>
    </div>
  );
}
