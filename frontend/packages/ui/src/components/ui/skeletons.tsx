/**
 * Reusable skeleton patterns built on the base `Skeleton` primitive.
 *
 * All are server-safe (no hooks / no 'use client') so they work in `loading.tsx`
 * and Suspense fallbacks as well as inside client components. Each matches the
 * shape of the content it stands in for, to avoid layout shift when data lands.
 */
import { Card, CardContent, CardHeader } from '../../primitives/card';
import { cn } from '../../utils';
import { Skeleton } from './skeleton';

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={cn('h-3 w-full', i === lines - 1 && 'w-2/3')} />
      ))}
    </div>
  );
}

export function SkeletonCard({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <Skeleton className="h-4 w-32" />
      </CardHeader>
      <CardContent>
        <SkeletonText lines={lines} />
      </CardContent>
    </Card>
  );
}

export function SkeletonForm({ fields = 5, className }: { fields?: number; className?: string }) {
  return (
    <div className={cn('space-y-5', className)}>
      {Array.from({ length: fields }).map((_, i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-9 w-full max-w-md" />
        </div>
      ))}
    </div>
  );
}

const _COL_WIDTHS = ['w-40', 'w-24', 'w-20', 'w-16'];

export function SkeletonTable({
  rows = 6,
  cols = 4,
  header = true,
  className,
}: {
  rows?: number;
  cols?: number;
  header?: boolean;
  className?: string;
}) {
  return (
    <div className={cn('w-full', className)}>
      {header ? (
        <div className="flex items-center gap-4 border-b px-3 py-2.5">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className={cn('h-3', _COL_WIDTHS[c % _COL_WIDTHS.length])} />
          ))}
        </div>
      ) : null}
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-4 px-3 py-3">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className={cn('h-4', _COL_WIDTHS[c % _COL_WIDTHS.length])} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonList({
  rows = 5,
  avatar = false,
  className,
}: {
  rows?: number;
  avatar?: boolean;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-3 py-2.5">
          {avatar ? <Skeleton className="h-10 w-10 shrink-0 rounded-full" /> : null}
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3 w-1/3" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Matches the KPICard body — label line + value line. */
export function SkeletonKpiValue() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-7 w-16" />
    </div>
  );
}

export function SkeletonChart({ bars = 8, className }: { bars?: number; className?: string }) {
  // Deterministic varied heights (no Math.random — keeps SSR stable).
  const heights = ['h-16', 'h-28', 'h-20', 'h-36', 'h-24', 'h-32', 'h-12', 'h-24'];
  return (
    <div className={cn('flex h-40 items-end gap-2', className)}>
      {Array.from({ length: bars }).map((_, i) => (
        <Skeleton key={i} className={cn('flex-1 rounded-md', heights[i % heights.length])} />
      ))}
    </div>
  );
}
