'use client';

import { Badge } from '@reloop/ui';
import type { Objection } from '../../types';

function severityVariant(severity: string): 'secondary' | 'warning' | 'destructive' {
  switch (severity.toLowerCase()) {
    case 'high':
      return 'destructive';
    case 'low':
      return 'secondary';
    default:
      return 'warning';
  }
}

export function ObjectionsList({ objections }: { objections: Objection[] }) {
  return (
    <div className="space-y-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        Obiezioni rilevate
      </p>

      {objections.length === 0 ? (
        <p className="text-[12px] italic text-muted-foreground/70">
          Nessuna obiezione rilevata in questa conversazione.
        </p>
      ) : (
        <ul className="space-y-2.5">
          {objections.map((o) => (
            <li
              key={o.id}
              className="rounded-lg border border-border bg-background/60 p-2.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[12px] font-medium capitalize">{o.category}</span>
                <Badge variant={severityVariant(o.severity)} className="shrink-0 capitalize">
                  {o.severity}
                </Badge>
              </div>
              <p className="mt-1 text-[12px] leading-snug text-muted-foreground">{o.summary}</p>
              {o.quote && (
                <p className="mt-1.5 border-l-2 border-border pl-2 text-[12px] italic leading-snug text-muted-foreground/80">
                  “{o.quote}”
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
