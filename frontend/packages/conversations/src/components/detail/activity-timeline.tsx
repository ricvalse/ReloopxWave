'use client';

import { Skeleton } from '@reloop/ui';
import {
  AlertTriangle,
  ArrowRightLeft,
  BookCheck,
  BookX,
  CalendarMinus,
  CalendarX,
  MessageCircle,
  MessageCircleOff,
  TrendingUp,
  UserCheck,
} from 'lucide-react';
import { useLeadActivity, type ActivityEvent } from '../../hooks/use-lead-activity';

interface ActivityTimelineProps {
  leadId: string | null | undefined;
}

const EVENT_CONFIG: Record<
  string,
  { label: string; Icon: React.ElementType; color: string }
> = {
  'booking.created': {
    label: 'Appuntamento prenotato',
    Icon: BookCheck,
    color: 'text-emerald-600',
  },
  'booking.failed': {
    label: 'Prenotazione fallita',
    Icon: BookX,
    color: 'text-red-500',
  },
  'booking.rescheduled': {
    label: 'Appuntamento spostato',
    Icon: CalendarMinus,
    color: 'text-amber-600',
  },
  'booking.cancelled': {
    label: 'Appuntamento cancellato',
    Icon: CalendarX,
    color: 'text-red-500',
  },
  'pipeline.moved': {
    label: 'Lead avanzato in pipeline',
    Icon: ArrowRightLeft,
    color: 'text-blue-600',
  },
  'pipeline.failed': {
    label: 'Avanzamento pipeline fallito',
    Icon: AlertTriangle,
    color: 'text-red-500',
  },
  'conversation.escalated': {
    label: 'Conversazione escalata',
    Icon: UserCheck,
    color: 'text-amber-600',
  },
  'lead_score_changed': {
    label: 'Score aggiornato',
    Icon: TrendingUp,
    color: 'text-purple-600',
  },
  'message.received': {
    label: 'Messaggio ricevuto',
    Icon: MessageCircle,
    color: 'text-muted-foreground',
  },
  'message.replied': {
    label: 'Risposta inviata',
    Icon: MessageCircle,
    color: 'text-muted-foreground',
  },
};

function relativeTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'adesso';
  if (mins < 60) return `${mins} min fa`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h fa`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} g fa`;
  return new Date(iso).toLocaleDateString('it-IT', { day: '2-digit', month: 'short' });
}

function EventDetail({ event }: { event: ActivityEvent }): React.ReactElement | null {
  const p = event.properties;
  const fragments: string[] = [];

  if (event.event_type === 'booking.created' || event.event_type === 'booking.rescheduled') {
    const slot = p.slot_start_iso ?? p.new_start;
    if (slot) {
      fragments.push(
        new Date(slot as string).toLocaleString('it-IT', {
          day: '2-digit',
          month: 'short',
          hour: '2-digit',
          minute: '2-digit',
        }),
      );
    }
  }
  if (event.event_type === 'booking.failed') {
    const reason = p.reason as string | undefined;
    if (reason) fragments.push(reason);
    const suggested = p.suggested as string[] | undefined;
    if (suggested?.length) fragments.push(`${suggested.length} slot alternativi proposti`);
  }
  if (event.event_type === 'pipeline.moved' || event.event_type === 'pipeline.failed') {
    const reason = (p.llm_reason ?? p.reason) as string | undefined;
    if (reason) fragments.push(reason);
  }
  if (event.event_type === 'lead_score_changed') {
    const prev = p.previous_score as number | undefined;
    const next = p.new_score as number | undefined;
    if (prev !== undefined && next !== undefined) fragments.push(`${prev} → ${next}`);
  }
  if (event.event_type === 'conversation.escalated') {
    const reason = p.reason as string | undefined;
    if (reason) fragments.push(reason);
  }

  if (!fragments.length) return null;
  return (
    <p className="mt-0.5 text-[11px] text-muted-foreground line-clamp-2">
      {fragments.join(' · ')}
    </p>
  );
}

export function ActivityTimeline({ leadId }: ActivityTimelineProps) {
  const { data, isLoading } = useLeadActivity(leadId);

  if (!leadId) return null;

  if (isLoading && !data) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  const events = (data ?? []).filter(
    (e) => !['message.received', 'message.replied'].includes(e.event_type),
  );

  if (!events.length) {
    return (
      <div className="flex flex-col items-center gap-1 py-4 text-center">
        <MessageCircleOff className="h-5 w-5 text-muted-foreground/50" />
        <p className="text-xs text-muted-foreground">Nessuna attività registrata</p>
      </div>
    );
  }

  return (
    <ol className="relative space-y-3 border-l border-border pl-4">
      {events.map((event) => {
        const cfg = EVENT_CONFIG[event.event_type] ?? {
          label: event.event_type,
          Icon: MessageCircle,
          color: 'text-muted-foreground',
        };
        const { Icon, color, label } = cfg;
        return (
          <li key={event.id} className="relative">
            <span className="absolute -left-[21px] flex h-4 w-4 items-center justify-center rounded-full bg-background ring-1 ring-border">
              <Icon className={`h-2.5 w-2.5 ${color}`} />
            </span>
            <div>
              <p className="text-xs font-medium leading-tight">{label}</p>
              <EventDetail event={event} />
              <p className="mt-0.5 text-[10px] text-muted-foreground/60">
                {relativeTime(event.occurred_at)}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
