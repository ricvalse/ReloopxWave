'use client';

import { useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  CardContent,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  SkeletonList,
} from '@reloop/ui';
import { CalendarClock, CalendarX2 } from 'lucide-react';
import {
  type Appointment,
  useAppointments,
  useCancelAppointment,
  useRescheduleAppointment,
} from './use-appointments';

// ---- date helpers (no date lib in the app — native Intl, honoring tz_name) --

function fmtTime(iso: string, tz: string | null): string {
  return new Intl.DateTimeFormat('it-IT', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: tz ?? undefined,
  }).format(new Date(iso));
}

function fmtDayLabel(iso: string, tz: string | null): string {
  return new Intl.DateTimeFormat('it-IT', {
    weekday: 'long',
    day: '2-digit',
    month: 'long',
    year: 'numeric',
    timeZone: tz ?? undefined,
  }).format(new Date(iso));
}

/** ISO → `YYYY-MM-DDTHH:mm` for <input type="datetime-local"> (browser-local). */
function toInputValue(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const STATUS_BADGE: Record<string, { label: string; variant: 'success' | 'destructive' | 'warning' | 'outline' }> = {
  booked: { label: 'Confermato', variant: 'success' },
  cancelled: { label: 'Annullato', variant: 'destructive' },
  noshow: { label: 'No-show', variant: 'warning' },
};

function StatusBadge({ status }: { status: string }) {
  const cfg = STATUS_BADGE[status] ?? { label: status, variant: 'outline' as const };
  return <Badge variant={cfg.variant}>{cfg.label}</Badge>;
}

function groupByDay(appointments: Appointment[]): { day: string; items: Appointment[] }[] {
  const groups: { day: string; items: Appointment[] }[] = [];
  for (const appt of appointments) {
    const day = fmtDayLabel(appt.start_at, appt.tz_name);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(appt);
    else groups.push({ day, items: [appt] });
  }
  return groups;
}

// ---- reschedule dialog -----------------------------------------------------

function RescheduleDialog({ appt }: { appt: Appointment }) {
  const reschedule = useRescheduleAppointment();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(() => toInputValue(appt.start_at));
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!value) {
      setError('Indica una nuova data e ora.');
      return;
    }
    try {
      // datetime-local is browser-local and naïve → send a full UTC ISO so the
      // backend stores the exact instant the merchant picked.
      await reschedule.mutateAsync({ id: appt.id, startAtIso: new Date(value).toISOString() });
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Errore imprevisto');
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Sposta
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Sposta appuntamento</DialogTitle>
          <DialogDescription>
            Scegli la nuova data e ora. La modifica viene applicata anche su GoHighLevel.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor={`resched-${appt.id}`}>Nuovo orario</Label>
          <Input
            id={`resched-${appt.id}`}
            type="datetime-local"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Annulla</Button>
          </DialogClose>
          <Button onClick={submit} disabled={reschedule.isPending}>
            {reschedule.isPending ? 'Salvataggio…' : 'Conferma'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---- cancel dialog ---------------------------------------------------------

function CancelDialog({ appt }: { appt: Appointment }) {
  const cancel = useCancelAppointment();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setError(null);
    try {
      await cancel.mutateAsync(appt.id);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Errore imprevisto');
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
        Annulla
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Annullare l’appuntamento?</DialogTitle>
          <DialogDescription>
            L’appuntamento verrà annullato anche su GoHighLevel. L’azione non è reversibile.
          </DialogDescription>
        </DialogHeader>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">No, torna indietro</Button>
          </DialogClose>
          <Button variant="destructive" onClick={confirm} disabled={cancel.isPending}>
            {cancel.isPending ? 'Annullamento…' : 'Sì, annulla'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---- row + list ------------------------------------------------------------

function AppointmentRow({ appt, actionable }: { appt: Appointment; actionable: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border/60 py-3 last:border-0">
      <div className="flex items-center gap-3">
        <span className="w-14 shrink-0 text-sm font-medium tabular-nums">
          {fmtTime(appt.start_at, appt.tz_name)}
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{appt.title || 'Appuntamento'}</p>
          {appt.source === 'bot' ? (
            <p className="text-xs text-muted-foreground">Creato dall’assistente</p>
          ) : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <StatusBadge status={appt.status} />
        {actionable ? (
          <>
            <RescheduleDialog appt={appt} />
            <CancelDialog appt={appt} />
          </>
        ) : null}
      </div>
    </div>
  );
}

function DayGroups({ appointments, actionable }: { appointments: Appointment[]; actionable: boolean }) {
  const groups = useMemo(() => groupByDay(appointments), [appointments]);
  return (
    <div className="space-y-5">
      {groups.map((g) => (
        <div key={g.day}>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {g.day}
          </h3>
          <Card>
            <CardContent className="px-4 py-1">
              {g.items.map((appt) => (
                <AppointmentRow key={appt.id} appt={appt} actionable={actionable} />
              ))}
            </CardContent>
          </Card>
        </div>
      ))}
    </div>
  );
}

export function AgendaPanel() {
  const query = useAppointments();
  const [tab, setTab] = useState<'upcoming' | 'past'>('upcoming');

  const { upcoming, past } = useMemo(() => {
    const now = Date.now();
    const all = query.data ?? [];
    const up = all
      .filter((a) => a.status === 'booked' && new Date(a.start_at).getTime() >= now)
      .sort((a, b) => +new Date(a.start_at) - +new Date(b.start_at));
    const pa = all
      .filter((a) => !(a.status === 'booked' && new Date(a.start_at).getTime() >= now))
      .sort((a, b) => +new Date(b.start_at) - +new Date(a.start_at));
    return { upcoming: up, past: pa };
  }, [query.data]);

  if (query.isLoading) {
    return <SkeletonList rows={6} />;
  }
  if (query.error) {
    return <p className="text-sm text-destructive">Errore nel caricamento dell’agenda.</p>;
  }
  if (!upcoming.length && !past.length) {
    return (
      <EmptyState
        icon={CalendarClock}
        title="Nessun appuntamento"
        description="Gli appuntamenti fissati dall’assistente (e quelli creati su GoHighLevel) compaiono qui."
      />
    );
  }

  const list = tab === 'upcoming' ? upcoming : past;

  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-lg border border-border p-1">
        <button
          type="button"
          onClick={() => setTab('upcoming')}
          className={`rounded-md px-3 py-1 text-sm ${tab === 'upcoming' ? 'bg-muted font-medium' : 'text-muted-foreground'}`}
        >
          Prossimi ({upcoming.length})
        </button>
        <button
          type="button"
          onClick={() => setTab('past')}
          className={`rounded-md px-3 py-1 text-sm ${tab === 'past' ? 'bg-muted font-medium' : 'text-muted-foreground'}`}
        >
          Passati / annullati ({past.length})
        </button>
      </div>

      {list.length ? (
        <DayGroups appointments={list} actionable={tab === 'upcoming'} />
      ) : (
        <EmptyState
          icon={CalendarX2}
          title={tab === 'upcoming' ? 'Nessun appuntamento in programma' : 'Nessuno storico'}
          description={
            tab === 'upcoming'
              ? 'Quando l’assistente fissa un appuntamento, lo vedi qui.'
              : 'Gli appuntamenti passati o annullati compaiono qui.'
          }
        />
      )}
    </div>
  );
}
