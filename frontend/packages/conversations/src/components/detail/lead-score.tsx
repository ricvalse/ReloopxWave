'use client';

import { Badge, cn } from '@reloop/ui';
import type { Lead } from '../../types';

/** Qualitative bucket + accent for a 0–100 lead score (arch doc thresholds). */
function scoreBand(score: number): { label: string; bar: string; text: string } {
  if (score >= 80) return { label: 'Caldo', bar: 'bg-success', text: 'text-success' };
  if (score >= 30) return { label: 'Tiepido', bar: 'bg-warning', text: 'text-warning' };
  return { label: 'Freddo', bar: 'bg-muted-foreground/50', text: 'text-muted-foreground' };
}

function sentimentBadge(
  sentiment: string | null,
): { label: string; variant: 'success' | 'secondary' | 'destructive' | 'outline' } | null {
  if (!sentiment) return null;
  switch (sentiment.toLowerCase()) {
    case 'positive':
      return { label: 'Positivo', variant: 'success' };
    case 'neutral':
      return { label: 'Neutro', variant: 'secondary' };
    case 'negative':
      return { label: 'Negativo', variant: 'destructive' };
    default:
      return { label: sentiment, variant: 'outline' };
  }
}

export function LeadScore({ lead }: { lead: Lead }) {
  const score = Math.max(0, Math.min(100, lead.score ?? 0));
  const band = scoreBand(score);
  const sentiment = sentimentBadge(lead.sentiment);

  return (
    <div className="space-y-3">
      <div className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Lead score
          </p>
          <div className="mt-0.5 flex items-baseline gap-1.5">
            <span className="text-3xl font-semibold tabular-nums leading-none">{score}</span>
            <span className="text-xs text-muted-foreground">/100</span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className={cn('text-xs font-semibold', band.text)}>{band.label}</span>
          {sentiment && <Badge variant={sentiment.variant}>{sentiment.label}</Badge>}
        </div>
      </div>

      {/* Score meter */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={cn('h-full rounded-full transition-all', band.bar)}
          style={{ width: `${score}%` }}
        />
      </div>

      {lead.score_reasons?.length > 0 && (
        <ul className="space-y-1 pt-0.5">
          {lead.score_reasons.map((reason, i) => (
            <li
              key={`${i}-${reason}`}
              className="flex gap-1.5 text-[12px] leading-snug text-muted-foreground"
            >
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
