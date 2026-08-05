'use client';

/**
 * Editor della settimana-tipo: sette righe, ognuna con un interruttore e una o
 * più fasce orarie.
 *
 * Vive nel design system perché lo usano **due** app: il merchant configura i
 * propri orari di risposta, l'agenzia imposta (e blocca) il default del
 * template. Erano due renderer di campo separati, e duplicare qui avrebbe
 * significato due editor da tenere allineati su un formato dati che il backend
 * valida in un modo solo.
 *
 * Due comportamenti non ovvi, entrambi voluti:
 *
 *  * un giorno spento **conserva** le sue fasce invece di svuotarle, così
 *    riaprire il giovedì non costringe a riscrivere gli orari che c'erano;
 *  * più fasce nello stesso giorno sono il modo di esprimere la pausa pranzo.
 *    Il backend tratta ogni fascia come una finestra a sé: 09:00-13:00 +
 *    15:00-19:00 significa che fra le 13 e le 15 il bot tace.
 */

import { Input } from '../components/ui/input';
import { Switch } from '../components/ui/switch';

/** 0=lunedì … 6=domenica — la stessa convenzione del backend. */
const GIORNI = ['Lunedì', 'Martedì', 'Mercoledì', 'Giovedì', 'Venerdì', 'Sabato', 'Domenica'];

export type WeeklyWindow = { start: string; end: string };
export type WeeklyDay = { day: number; enabled: boolean; windows: WeeklyWindow[] };

const FASCIA_DEFAULT: WeeklyWindow = { start: '09:00', end: '18:00' };
const MAX_FASCE = 4;

/** Riporta un valore JSONB arbitrario a sette righe ordinate. */
export function normalizeWeekly(value: unknown): WeeklyDay[] {
  const perGiorno = new Map<number, WeeklyDay>();
  if (Array.isArray(value)) {
    for (const raw of value) {
      if (!raw || typeof raw !== 'object') continue;
      const g = raw as Partial<WeeklyDay>;
      if (typeof g.day !== 'number' || g.day < 0 || g.day > 6) continue;
      perGiorno.set(g.day, {
        day: g.day,
        enabled: g.enabled !== false,
        windows: Array.isArray(g.windows)
          ? g.windows
              .filter((w): w is WeeklyWindow => !!w && typeof w === 'object')
              .map((w) => ({ start: String(w.start ?? ''), end: String(w.end ?? '') }))
          : [],
      });
    }
  }
  // Sempre sette righe: un giorno mancante è "chiuso" e va comunque mostrato,
  // altrimenti l'utente non ha modo di riaprirlo.
  return Array.from(
    { length: 7 },
    (_, d) => perGiorno.get(d) ?? { day: d, enabled: false, windows: [{ ...FASCIA_DEFAULT }] },
  );
}

export function WeeklyHoursEditor({
  value,
  disabled = false,
  onChange,
  idPrefix = 'weekly',
}: {
  value: unknown;
  disabled?: boolean;
  onChange: (v: WeeklyDay[]) => void;
  idPrefix?: string;
}) {
  const settimana = normalizeWeekly(value);

  const patchGiorno = (day: number, patch: Partial<WeeklyDay>) =>
    onChange(settimana.map((g) => (g.day === day ? { ...g, ...patch } : g)));
  const patchFascia = (day: number, idx: number, patch: Partial<WeeklyWindow>) => {
    // Cercato e non indicizzato: `normalizeWeekly` garantisce sette righe, ma
    // il tipo `WeeklyDay[]` no, e un accesso posizionale qui sarebbe un
    // `undefined` silenzioso il giorno in cui la normalizzazione cambia.
    const giorno = settimana.find((g) => g.day === day);
    if (!giorno) return;
    patchGiorno(day, {
      windows: giorno.windows.map((w, i) => (i === idx ? { ...w, ...patch } : w)),
    });
  };

  const nessunGiornoAperto = !settimana.some((g) => g.enabled && g.windows.length > 0);

  return (
    <div className="w-full space-y-1.5">
      {settimana.map((g) => (
        <div
          key={g.day}
          className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-border/60 px-3 py-2"
        >
          <Switch
            id={`${idPrefix}-${g.day}`}
            disabled={disabled}
            checked={g.enabled}
            onCheckedChange={(on) =>
              patchGiorno(g.day, {
                enabled: on,
                // Riaccendere un giorno senza fasce lo lascerebbe chiuso in
                // silenzio: se non ne ha, gliene diamo una di partenza.
                windows: g.windows.length ? g.windows : [{ ...FASCIA_DEFAULT }],
              })
            }
          />
          <span className="w-24 shrink-0 text-sm font-medium">{GIORNI[g.day]}</span>

          {!g.enabled ? (
            <span className="text-sm text-muted-foreground">Chiuso</span>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {g.windows.map((w, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <Input
                    type="time"
                    aria-label={`${GIORNI[g.day]} — apertura ${i + 1}`}
                    disabled={disabled}
                    value={w.start}
                    onChange={(e) => patchFascia(g.day, i, { start: e.target.value })}
                    className="w-[7.5rem] tabular-nums"
                  />
                  <span aria-hidden className="text-muted-foreground">
                    →
                  </span>
                  <Input
                    type="time"
                    aria-label={`${GIORNI[g.day]} — chiusura ${i + 1}`}
                    disabled={disabled}
                    value={w.end}
                    onChange={(e) => patchFascia(g.day, i, { end: e.target.value })}
                    className="w-[7.5rem] tabular-nums"
                  />
                  {g.windows.length > 1 && (
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() =>
                        patchGiorno(g.day, { windows: g.windows.filter((_, j) => j !== i) })
                      }
                      className="rounded px-1.5 py-1 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                      aria-label={`Rimuovi fascia ${i + 1} di ${GIORNI[g.day]}`}
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
              {g.windows.length < MAX_FASCE && (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    patchGiorno(g.day, {
                      windows: [...g.windows, { start: '15:00', end: '19:00' }],
                    })
                  }
                  className="rounded px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                >
                  + fascia
                </button>
              )}
            </div>
          )}
        </div>
      ))}

      {nessunGiornoAperto && (
        <p className="text-sm text-warning">
          Nessun giorno aperto: con «Orari personalizzati» il bot non risponderebbe mai. Per
          spegnere l’assistente usa invece «Risposta automatica».
        </p>
      )}
    </div>
  );
}
