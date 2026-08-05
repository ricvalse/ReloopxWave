"""Risoluzione degli orari di risposta di un merchant (config + DB).

`ai_core.scheduling` sa *ragionare* su una settimana-tipo ma non sa da dove
arriva. Questo modulo è il pezzo che la costruisce, e le fonti sono tre a
seconda di `schedule.mode`:

  * `always`         — nessun vincolo: il bot risponde sempre (default storico).
  * `business_hours` — gli orari di apertura che il merchant ha già inserito per
    le prenotazioni (tabella `business_hours` + `business_closures`, le stesse
    righe che il booking usa e che il cron notturno sincronizza col calendario
    GHL). Riusarle evita la doppia manutenzione: chi cambia l'orario del negozio
    non deve ricordarsi di cambiarlo una seconda volta per il bot.
  * `custom`         — una settimana-tipo tutta sua, in `schedule.weekly`,
    per chi vuole che l'assistente risponda oltre l'orario di sportello.

Il risultato è un `ResponseHours` che risponde a due domande — "posso rispondere
adesso?" e "quando potrò?" — perché la seconda è ciò che permette allo sweep di
ripresa di riportare in vita una conversazione lasciata in sospeso.

Tutto è **fail-open**: qualunque errore di risoluzione produce "sempre aperto".
Un bug qui deve degradare in un bot che risponde troppo, mai in un bot muto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from ai_core.scheduling import (
    WeeklySchedule,
    always_open,
    is_open_at,
    next_opening_after,
    resolve_timezone,
    schedule_from_windows,
)
from config_resolver import ConfigResolver
from config_resolver.schema import ConfigKey
from shared import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResponseHours:
    """Gli orari risolti di un merchant, pronti da interrogare."""

    mode: str
    tz: ZoneInfo
    schedule: WeeklySchedule
    off_hours_message: str | None
    off_hours_message_once: bool
    resume_on_open: bool
    apply_to_automations: bool

    @property
    def unrestricted(self) -> bool:
        """True quando non c'è nessun vincolo orario da applicare."""
        return self.mode == "always"

    def is_open(self, moment: datetime | None = None) -> bool:
        if self.unrestricted:
            return True
        return is_open_at(self.schedule, self.tz, moment or datetime.now(tz=UTC))

    def next_opening(self, moment: datetime | None = None) -> datetime | None:
        """Prossimo istante utile, o None se l'agenda non riapre mai."""
        if self.unrestricted:
            return moment or datetime.now(tz=UTC)
        return next_opening_after(self.schedule, self.tz, moment or datetime.now(tz=UTC))


_UNRESTRICTED = ResponseHours(
    mode="always",
    tz=resolve_timezone(None),
    schedule=always_open(),
    off_hours_message=None,
    off_hours_message_once=True,
    resume_on_open=True,
    apply_to_automations=False,
)


def _weekly_from_config(raw: Any) -> dict[int, list[tuple[str, str]]]:
    """`schedule.weekly` (lista di dict dal JSONB) → finestre per giorno.

    Arriva dalla cascata come JSON grezzo, non come modello Pydantic: la
    validazione è avvenuta in scrittura, qui si legge in modo difensivo perché
    un bag salvato da una versione precedente non deve far cadere il turno.
    """
    out: dict[int, list[tuple[str, str]]] = {}
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        day = entry.get("day")
        if not isinstance(day, int) or not 0 <= day <= 6:
            continue
        if not entry.get("enabled", True):
            continue  # giorno di chiusura: le finestre restano salvate ma inerti
        windows: list[tuple[str, str]] = []
        for w in entry.get("windows") or []:
            if isinstance(w, dict) and w.get("start") and w.get("end"):
                windows.append((str(w["start"]), str(w["end"])))
        if windows:
            out[day] = windows
    return out


def _weekly_from_business_hours(rows: list[Any]) -> dict[int, list[tuple[str, str]]]:
    """Righe `BusinessHour` → finestre per giorno, pausa pranzo esplosa.

    Una pausa spezza il giorno in due finestre distinte, che è esattamente il
    modo in cui il booking già tratta quelle stesse righe: fuori pausa il bot
    risponde, durante no. Se la pausa è incoerente (inizio dopo fine, o fuori
    dall'orario) viene ignorata e il giorno resta una finestra sola — meglio
    rispondere un'ora in più che tacere per un dato sporco.
    """
    out: dict[int, list[tuple[str, str]]] = {}
    for row in rows:
        if not getattr(row, "is_open", False):
            continue
        open_t, close_t = row.open_time, row.close_time
        if open_t is None or close_t is None:
            continue
        fmt = "%H:%M"
        bs, be = row.break_start, row.break_end
        has_break = bs is not None and be is not None and open_t < bs < be < close_t
        if has_break:
            out[row.day_of_week] = [
                (open_t.strftime(fmt), bs.strftime(fmt)),
                (be.strftime(fmt), close_t.strftime(fmt)),
            ]
        else:
            out[row.day_of_week] = [(open_t.strftime(fmt), close_t.strftime(fmt))]
    return out


async def resolve_response_hours(session: Any, merchant_id: UUID) -> ResponseHours:
    """Costruisce gli orari di risposta effettivi del merchant.

    Fail-open per contratto: se la cascata o il DB non rispondono, il merchant
    risulta sempre aperto. Il costo di sbagliare in quella direzione è un bot
    che risponde alle 3 di notte; nell'altra, un bot che non risponde mai e
    nessuno se ne accorge finché non arriva il reclamo.
    """
    try:
        resolver = ConfigResolver(session)
        mode = await resolver.resolve(ConfigKey.SCHEDULE_MODE, merchant_id=merchant_id)
        mode = str(mode or "always")

        tz = resolve_timezone(
            str(await resolver.resolve(ConfigKey.SCHEDULE_TIMEZONE, merchant_id=merchant_id) or "")
        )
        raw_message = await resolver.resolve(
            ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE, merchant_id=merchant_id
        )
        message = (
            raw_message.strip() if isinstance(raw_message, str) and raw_message.strip() else None
        )
        once = bool(
            await resolver.resolve(
                ConfigKey.SCHEDULE_OFF_HOURS_MESSAGE_ONCE, merchant_id=merchant_id
            )
        )
        resume = bool(
            await resolver.resolve(ConfigKey.SCHEDULE_RESUME_ON_OPEN, merchant_id=merchant_id)
        )
        automations = bool(
            await resolver.resolve(ConfigKey.SCHEDULE_APPLY_TO_AUTOMATIONS, merchant_id=merchant_id)
        )

        if mode not in {"business_hours", "custom"}:
            return ResponseHours(
                mode="always",
                tz=tz,
                schedule=always_open(),
                off_hours_message=message,
                off_hours_message_once=once,
                resume_on_open=resume,
                apply_to_automations=automations,
            )

        closures: frozenset[date] = frozenset()
        if mode == "business_hours":
            from db.repositories.services import (
                BusinessClosureRepository,
                BusinessHourRepository,
            )

            rows = await BusinessHourRepository(session).list(merchant_id)
            day_windows = _weekly_from_business_hours(rows)
            closures = frozenset(
                c.closed_on
                for c in await BusinessClosureRepository(session).list(
                    merchant_id, from_date=datetime.now(tz=tz).date()
                )
            )
            if not day_windows:
                # Modalità "segui gli orari di apertura" senza orari inseriti:
                # è una configurazione incompleta, non la volontà di stare
                # zitti. Resta sempre aperto e lascia traccia.
                logger.warning(
                    "schedule.business_hours_empty",
                    merchant_id=str(merchant_id),
                )
                return ResponseHours(
                    mode="always",
                    tz=tz,
                    schedule=always_open(),
                    off_hours_message=message,
                    off_hours_message_once=once,
                    resume_on_open=resume,
                    apply_to_automations=automations,
                )
        else:
            raw_weekly = await resolver.resolve(ConfigKey.SCHEDULE_WEEKLY, merchant_id=merchant_id)
            day_windows = _weekly_from_config(raw_weekly)
            if not day_windows:
                logger.warning("schedule.custom_weekly_empty", merchant_id=str(merchant_id))
                return ResponseHours(
                    mode="always",
                    tz=tz,
                    schedule=always_open(),
                    off_hours_message=message,
                    off_hours_message_once=once,
                    resume_on_open=resume,
                    apply_to_automations=automations,
                )

        return ResponseHours(
            mode=mode,
            tz=tz,
            schedule=schedule_from_windows(day_windows, closures=closures),
            off_hours_message=message,
            off_hours_message_once=once,
            resume_on_open=resume,
            apply_to_automations=automations,
        )
    except Exception as e:  # pragma: no cover — difesa, non flusso
        logger.warning("schedule.resolve_failed", error=str(e), merchant_id=str(merchant_id))
        return _UNRESTRICTED
