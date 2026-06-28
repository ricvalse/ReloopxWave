'use client';

import { useMemo } from 'react';
import { Calendar, dateFnsLocalizer, type Event } from 'react-big-calendar';
import { format, getDay, parse, startOfWeek } from 'date-fns';
import { it } from 'date-fns/locale';
import { type Appointment } from './use-appointments';
import './agenda-calendar.css';
import 'react-big-calendar/lib/css/react-big-calendar.css';

const localizer = dateFnsLocalizer({
  format,
  parse,
  startOfWeek: (d: Date) => startOfWeek(d, { locale: it }),
  getDay,
  locales: { it },
});

const MESSAGES = {
  allDay: 'Tutto il giorno',
  previous: '‹',
  next: '›',
  today: 'Oggi',
  month: 'Mese',
  week: 'Settimana',
  day: 'Giorno',
  agenda: 'Agenda',
  date: 'Data',
  time: 'Ora',
  event: 'Evento',
  noEventsInRange: 'Nessun appuntamento in questo periodo.',
  showMore: (count: number) => `+${count} altri`,
};

const SCROLL_TO_8AM = new Date(1970, 1, 1, 8, 0, 0);

type CalEvent = Event & { resource: Appointment };

function MonthEventContent({ event }: { event: CalEvent }) {
  const isLocal = !event.resource.ghl_appointment_id;
  return (
    <span className="flex items-center gap-1 overflow-hidden">
      <span className="truncate text-[11px]">{event.title ?? 'Appuntamento'}</span>
      {isLocal ? (
        <span className="shrink-0 rounded bg-black/20 px-1 text-[9px] font-semibold leading-4">L</span>
      ) : null}
    </span>
  );
}

function TimeEventContent({ event }: { event: CalEvent }) {
  const isLocal = !event.resource.ghl_appointment_id;
  const startTime = event.start instanceof Date ? format(event.start, 'HH:mm') : null;
  return (
    <div className="overflow-hidden leading-tight">
      {startTime ? (
        <p className="text-[10px] font-medium opacity-75">{startTime}</p>
      ) : null}
      <p className="truncate text-[11px] font-semibold">{event.title ?? 'Appuntamento'}</p>
      {isLocal ? (
        <span className="mt-0.5 inline-block rounded bg-black/20 px-1 text-[9px] font-semibold uppercase tracking-wide leading-4">
          locale
        </span>
      ) : null}
    </div>
  );
}

type Props = {
  appointments: Appointment[];
  onSelectEvent: (appt: Appointment) => void;
};

export function AgendaCalendar({ appointments, onSelectEvent }: Props) {
  const events = useMemo<CalEvent[]>(
    () =>
      appointments
        .filter((a) => a.status !== 'cancelled')
        .map((a) => ({
          id: a.id,
          title: a.title ?? 'Appuntamento',
          start: new Date(a.start_at),
          end: new Date(a.end_at ?? a.start_at),
          resource: a,
        })),
    [appointments],
  );

  function eventPropGetter(event: CalEvent) {
    const isLocal = !event.resource.ghl_appointment_id;
    const isNoshow = event.resource.status === 'noshow';
    return {
      className: isNoshow
        ? 'rbc-event--noshow'
        : isLocal
          ? 'rbc-event--local-only'
          : '',
    };
  }

  return (
    <div className="agenda-calendar-wrapper h-[680px]">
      <Calendar
        localizer={localizer}
        events={events}
        culture="it"
        messages={MESSAGES}
        defaultView="week"
        views={['month', 'week', 'day', 'agenda']}
        eventPropGetter={eventPropGetter}
        components={{
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          event: TimeEventContent as any,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          month: { event: MonthEventContent as any },
        }}
        onSelectEvent={(e) => onSelectEvent((e as CalEvent).resource)}
        scrollToTime={SCROLL_TO_8AM}
        popup
      />
    </div>
  );
}
