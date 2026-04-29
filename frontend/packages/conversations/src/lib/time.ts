/**
 * Relative time formatter for the WhatsApp-inspired thread list.
 *
 *   today       -> "HH:mm"            (e.g. "12:45")
 *   yesterday   -> "Ieri"
 *   this week   -> "lun", "mar", ...   (Italian weekday short)
 *   older       -> "dd/MM/yy"
 */
const WEEKDAYS_IT = ['dom', 'lun', 'mar', 'mer', 'gio', 'ven', 'sab'] as const;

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function formatThreadTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';

  const todayStart = startOfDay(now);
  const dayStart = startOfDay(d);
  const diffDays = Math.round((todayStart - dayStart) / 86_400_000);

  if (diffDays <= 0) {
    return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
  }
  if (diffDays === 1) return 'Ieri';
  if (diffDays < 7) return WEEKDAYS_IT[d.getDay()] ?? '';
  return d.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit', year: '2-digit' });
}

/**
 * Day separator label inside the message list: "Oggi" / "Ieri" / weekday for
 * the last week, full date otherwise.
 */
export function formatDaySeparator(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const todayStart = startOfDay(now);
  const dayStart = startOfDay(d);
  const diffDays = Math.round((todayStart - dayStart) / 86_400_000);

  if (diffDays === 0) return 'Oggi';
  if (diffDays === 1) return 'Ieri';
  if (diffDays < 7) return d.toLocaleDateString('it-IT', { weekday: 'long' });
  return d.toLocaleDateString('it-IT', { day: '2-digit', month: 'long', year: 'numeric' });
}

/** Time-only (HH:mm) — used inside the message bubble. */
export function formatBubbleTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
}

/** Are two timestamps on the same calendar day (local timezone)? */
export function isSameDay(a: string, b: string): boolean {
  return startOfDay(new Date(a)) === startOfDay(new Date(b));
}
