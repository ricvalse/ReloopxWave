import { formatDaySeparator } from '../lib/time';

export function DaySeparator({ iso }: { iso: string }) {
  return (
    <div className="my-3 flex items-center justify-center">
      <span className="rounded-full bg-background/85 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground shadow-sm backdrop-blur-sm">
        {formatDaySeparator(iso)}
      </span>
    </div>
  );
}
