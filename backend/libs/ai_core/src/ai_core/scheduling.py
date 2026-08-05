"""Orari di risposta dell'assistente (CC-CONFIG / UC-01) — logica pura.

Il merchant sceglie **quando il bot risponde**: sempre, oppure dentro una
settimana-tipo. Fuori da quella finestra il bot tace e la domanda del cliente
resta *sospesa*; è lo sweep di ripresa (`resume_after_hours`) a rispondere alla
riapertura, ed è per questo che qui servono **due** domande e non una:

  * `is_open_at(...)`      — posso rispondere adesso?
  * `next_opening_after(...)` — quando potrò? (None = mai più, agenda vuota)

Senza la seconda, "il bot ricomincia a rispondere quando si rientra negli
orari" non sarebbe verificabile: lo sweep saprebbe solo che è chiuso, non
distinguerebbe una chiusura notturna da un'agenda senza nemmeno un giorno
aperto.

Modello: sette giorni (0=lunedì … 6=domenica, la stessa convenzione di
`BusinessHour.day_of_week` e di `datetime.weekday()`), ciascuno con zero o più
finestre. Più finestre nello stesso giorno sono ciò che rende esprimibile la
pausa pranzo (09:00-13:00 + 15:00-19:00) senza campi dedicati. Una finestra il
cui `end` non segue `start` scavalca la mezzanotte e finisce nel giorno dopo.

Le date in `closures` (ferie, festività) cancellano le finestre che *iniziano*
in quel giorno; una finestra notturna aperta la sera prima prosegue, perché è
già cominciata quando il negozio era aperto.

Tutto il ragionamento avviene sul **wall-clock locale** del merchant, quindi i
confronti passano per `ZoneInfo`: un'ora di apertura è "le 9 di mattina qui",
non un offset fisso, e deve restare tale attraverso i cambi d'ora.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Rome"

# Quanto avanti guardare cercando la prossima apertura. Due settimane coprono
# qualunque settimana-tipo (che si ripete su 7 giorni) più un ponte di ferie
# lungo; oltre, l'agenda è di fatto vuota e restituiamo None invece di ciclare.
_LOOKAHEAD_DAYS = 21

_ALWAYS = {"", "24/7", "24x7", "24-7", "always", "sempre"}


def resolve_timezone(tz_name: str | None) -> ZoneInfo:
    """`ZoneInfo` del merchant, con fallback a Europe/Rome.

    Un fuso scritto male non deve mai far esplodere il turno di conversazione:
    a valle di questa funzione c'è la decisione "rispondo o no", e un'eccezione
    lì significherebbe un cliente lasciato in silenzio.
    """
    try:
        return ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Una finestra di apertura nel wall-clock locale.

    `end <= start` significa che scavalca la mezzanotte (es. 22:00-06:00).
    """

    start: time
    end: time

    @property
    def wraps_midnight(self) -> bool:
        return self.end <= self.start


@dataclass(frozen=True, slots=True)
class WeeklySchedule:
    """Settimana-tipo: giorno (0=lun … 6=dom) → finestre di quel giorno.

    Un giorno assente o con lista vuota è chiuso. `closures` sono date di
    chiusura straordinaria.
    """

    days: dict[int, tuple[TimeWindow, ...]]
    closures: frozenset[date] = frozenset()

    @property
    def is_never_open(self) -> bool:
        return not any(self.days.get(d) for d in range(7))


def parse_hhmm(token: str) -> time | None:
    """`"09:30"` → `time(9, 30)`. None se non è un orario valido.

    Tollerante per scelta: questi valori arrivano da una config editabile e da
    un import GHL, e un formato inatteso deve degradare, non sollevare.
    """
    try:
        hh, mm = str(token).strip().split(":", 1)
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return time(hour=h, minute=m)


def _intervals_starting_on(
    day: date, schedule: WeeklySchedule, tz: ZoneInfo
) -> list[tuple[datetime, datetime]]:
    """Gli intervalli assoluti che *iniziano* nel giorno `day`.

    Costruire intervalli assoluti (invece di confrontare ore) è ciò che rende
    corretti sia lo scavalco di mezzanotte sia i cambi d'ora: la somma di un
    `timedelta` a un datetime aware attraversa il cambio DST, mentre un
    confronto fra `(ora, minuto)` no.
    """
    if day in schedule.closures:
        return []
    out: list[tuple[datetime, datetime]] = []
    for window in schedule.days.get(day.weekday(), ()):
        start_dt = datetime.combine(day, window.start, tzinfo=tz)
        end_day = day + timedelta(days=1) if window.wraps_midnight else day
        end_dt = datetime.combine(end_day, window.end, tzinfo=tz)
        if end_dt > start_dt:
            out.append((start_dt, end_dt))
    return out


def _intervals_around(
    moment: datetime, schedule: WeeklySchedule, tz: ZoneInfo, *, days_ahead: int
) -> list[tuple[datetime, datetime]]:
    """Intervalli ordinati da ieri fino a `days_ahead` giorni avanti.

    Si parte da *ieri* perché una finestra notturna aperta la sera precedente è
    ancora in corso adesso: guardare solo da oggi la perderebbe, e il bot
    tacerebbe alle 2 di notte pur essendo dentro la finestra 22:00-06:00.
    """
    today = moment.astimezone(tz).date()
    intervals: list[tuple[datetime, datetime]] = []
    for offset in range(-1, days_ahead + 1):
        intervals.extend(_intervals_starting_on(today + timedelta(days=offset), schedule, tz))
    intervals.sort()
    return intervals


def is_open_at(schedule: WeeklySchedule, tz: ZoneInfo, moment: datetime) -> bool:
    """True se `moment` cade dentro una finestra di apertura."""
    for start, end in _intervals_around(moment, schedule, tz, days_ahead=1):
        if start <= moment < end:
            return True
    return False


def next_opening_after(schedule: WeeklySchedule, tz: ZoneInfo, moment: datetime) -> datetime | None:
    """Primo istante da `moment` in cui il bot potrà rispondere.

    Restituisce `moment` stesso se è già dentro una finestra (il chiamante non
    deve aspettare), e None se nelle prossime tre settimane non c'è nessuna
    apertura — un'agenda tutta chiusa, che lo sweep deve saper distinguere da
    "riapre domani" per non trattenere una conversazione all'infinito.
    """
    for start, end in _intervals_around(moment, schedule, tz, days_ahead=_LOOKAHEAD_DAYS):
        if start <= moment < end:
            return moment
        if start > moment:
            return start
    return None


def always_open() -> WeeklySchedule:
    """Settimana sempre aperta — l'assistente non ha vincoli orari."""
    full = (TimeWindow(start=time(0, 0), end=time(0, 0)),)  # wrappa: copre 24h
    return WeeklySchedule(days=dict.fromkeys(range(7), full))


def schedule_from_windows(
    day_windows: dict[int, list[tuple[str, str]]],
    *,
    closures: frozenset[date] = frozenset(),
) -> WeeklySchedule:
    """Costruisce una `WeeklySchedule` da orari testuali `"HH:MM"`.

    Le finestre non parsabili vengono scartate silenziosamente: la validazione
    del formato è compito dello schema Pydantic in scrittura, qui a valle
    conviene ignorare il rumore piuttosto che far fallire un turno.
    """
    days: dict[int, tuple[TimeWindow, ...]] = {}
    for dow, windows in day_windows.items():
        if not 0 <= dow <= 6:
            continue
        parsed: list[TimeWindow] = []
        for raw_start, raw_end in windows:
            start = parse_hhmm(raw_start)
            end = parse_hhmm(raw_end)
            if start is None or end is None:
                continue
            parsed.append(TimeWindow(start=start, end=end))
        if parsed:
            days[dow] = tuple(parsed)
    return WeeklySchedule(days=days, closures=closures)


def is_within_active_hours(active_hours: str | None, tz_name: str | None, now: datetime) -> bool:
    """Formato storico a finestra unica (`"24/7"` o `"HH:MM-HH:MM"`).

    Resta qui per un solo motivo: la migrazione 0049 converte i valori salvati
    nella nuova settimana-tipo, e i test di conversione devono poter verificare
    che il comportamento vecchio e quello nuovo coincidano. Il pipeline di
    conversazione non la chiama più.

    Parsing volutamente permissivo: ciò che non si capisce vale "sempre
    attivo", così un refuso non poteva zittire il bot di un merchant.
    """
    spec = (active_hours or "").strip().lower()
    if spec in _ALWAYS:
        return True

    try:
        start_token, end_token = spec.split("-", 1)
    except (ValueError, AttributeError):
        return True
    start = parse_hhmm(start_token)
    end = parse_hhmm(end_token)
    if start is None or end is None:
        return True  # illeggibile → fail open

    tz = resolve_timezone(tz_name)
    local = now.astimezone(tz)
    current = (local.hour, local.minute)
    start_t = (start.hour, start.minute)
    end_t = (end.hour, end.minute)

    if start_t == end_t:
        return True  # finestra nulla/piena → sempre attivo
    if start_t < end_t:
        return start_t <= current < end_t
    return current >= start_t or current < end_t
