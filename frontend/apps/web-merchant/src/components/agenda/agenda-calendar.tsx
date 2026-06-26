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

type CalEvent = Event & { resource: Appointment };

function EventContent({ event }: { event: CalEvent }) {
  const isLocal = !event.resource.ghl_appointment_id;
  return (
    <span className="flex items-center gap-1 overflow-hidden">
      <span className="truncate">{event.title ?? 'Appuntamento'}</span>
      {isLocal ? (
        <span className="shrink-0 rounded bg-black/20 px-1 text-[10px] font-semibold leading-4">
          locale
        </span>
      ) : null}
    </span>
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
    return { className: isLocal ? 'rbc-event--local-only' : '' };
  }

  return (
    <div className="agenda-calendar-wrapper h-[620px]">
      <Calendar
        localizer={localizer}
        events={events}
        culture="it"
        messages={MESSAGES}
        defaultView="month"
        views={['month', 'week', 'day']}
        eventPropGetter={eventPropGetter}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        components={{ event: EventContent as any }}
        onSelectEvent={(e) => onSelectEvent((e as CalEvent).resource)}
        popup
      />
    </div>
  );
}
