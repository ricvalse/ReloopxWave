"""Orari di risposta: marcatore di risposta sospesa + `schedule.active_hours` → `mode`/`weekly`.

Due cose, legate dallo stesso cambiamento funzionale.

**1. `conversations.off_hours_pending_at`.** Fuori orario il bot non risponde
più "ti risponderemo al più presto" e poi dimentica la domanda: la segna come
sospesa qui, e lo sweep `resume_after_hours` la riprende alla riapertura.
L'indice è parziale — le righe con marcatore sono una frazione minuscola della
tabella e lo sweep gira ogni cinque minuti, quindi l'indice deve pesare quanto
le sole conversazioni in attesa, non quanto lo storico.

**2. Conversione della configurazione.** `schedule.active_hours` era una
stringa a fascia unica (`"24/7"` oppure `"09:00-18:00"`) applicata identica a
tutti e sette i giorni: non sapeva dire "il sabato chiudo prima" né esprimere
la pausa pranzo. La sostituiscono `schedule.mode` (always | business_hours |
custom) e `schedule.weekly`.

Il campo **non** viene lasciato in vita come relitto inerte. Questo repo ha già
pagato quel prezzo con le `no_answer.*`, rimaste esposte nel pannello merchant
mentre nessuna riga di codice le leggeva più (rimosse in 0048, di cui questa
migrazione riusa la meccanica). Un orario che l'utente imposta e che il bot
ignora è peggio di un campo assente.

`BotConfigSchema` ha `extra="forbid"` e viene applicato **anche in lettura**
(`GET /bot-config/{id}/resolved`): lasciare `active_hours` nei bag salvati dopo
averlo tolto dal modello significherebbe un 500 sul pannello di chiunque lo
avesse toccato. Da qui la conversione, che è ciò che rende la rimozione
indolore.

Le tre superfici che contengono un override-bag con la stessa forma:
  * `bot_configs.overrides`            — override del merchant
  * `bot_templates.defaults`           — default del template d'agenzia
  * `conversation_profiles.overrides`  — profili di conversazione (ADR 0022)

più `bot_templates.locked_keys`, dove una `schedule.active_hours` rimasta
farebbe fallire il prossimo salvataggio del template (le chiavi lì dentro sono
validate contro `ConfigKey`).

La conversione gira in Python e non in SQL: costruire sette giorni di JSON da
una stringa dentro un `UPDATE` sarebbe illeggibile, e qui la chiarezza vale più
della singola query.

Downgrade: ricostruisce `active_hours` dalla settimana-tipo quando questa è
ancora esprimibile come fascia unica uguale tutti i giorni, altrimenti riscrive
`"24/7"`. Una settimana-tipo con pausa pranzo non ha una controparte nel
vecchio formato — quel dettaglio si perde, ed è la ragione per cui il rollback
del codice va fatto prima che i merchant configurino orari articolati.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0049_response_schedule"
down_revision: str | Sequence[str] | None = "0048_drop_no_answer_config_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BAG_SURFACES = (
    ("bot_configs", "overrides"),
    ("bot_templates", "defaults"),
    ("conversation_profiles", "overrides"),
)

# Gli stessi valori che il vecchio parser trattava come "sempre attivo".
_ALWAYS = {"", "24/7", "24x7", "24-7", "always", "sempre"}


def _parse_hhmm(token: str) -> str | None:
    """`"9:00"` → `"09:00"`; None se non è un orario valido."""
    try:
        hh, mm = str(token).strip().split(":", 1)
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def _weekly_from_active_hours(spec: str) -> list[dict[str, Any]] | None:
    """`"09:00-18:00"` → settimana-tipo con quella fascia tutti i giorni.

    None quando la stringa valeva "sempre attivo" o era illeggibile: in
    entrambi i casi il vecchio parser rispondeva sempre (falliva aperto), e la
    modalità `always` riproduce quel comportamento esattamente.
    """
    cleaned = (spec or "").strip().lower()
    if cleaned in _ALWAYS:
        return None
    try:
        start_token, end_token = cleaned.split("-", 1)
    except (ValueError, AttributeError):
        return None
    start = _parse_hhmm(start_token)
    end = _parse_hhmm(end_token)
    if start is None or end is None or start == end:
        return None
    # Tutti e sette i giorni aperti: è ciò che la fascia unica significava.
    return [
        {"day": d, "enabled": True, "windows": [{"start": start, "end": end}]} for d in range(7)
    ]


def _active_hours_from_weekly(weekly: Any) -> str | None:
    """Inverso di `_weekly_from_active_hours`, quando è possibile."""
    if not isinstance(weekly, list) or not weekly:
        return None
    windows: set[tuple[str, str]] = set()
    open_days = 0
    for entry in weekly:
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        ws = entry.get("windows") or []
        if len(ws) != 1 or not isinstance(ws[0], dict):
            return None  # pausa pranzo o giorno vuoto: non rappresentabile
        windows.add((str(ws[0].get("start")), str(ws[0].get("end"))))
        open_days += 1
    if open_days != 7 or len(windows) != 1:
        return None  # giorni diversi fra loro: non rappresentabile
    start, end = next(iter(windows))
    return f"{start}-{end}"


def _rewrite_bags(conn: sa.Connection, *, forward: bool) -> None:
    for table, column in _BAG_SURFACES:
        rows = conn.execute(
            sa.text(f"SELECT id, {column} FROM {table} WHERE jsonb_exists({column}, 'schedule')")
        ).fetchall()
        for row_id, bag in rows:
            bag = dict(bag or {})
            schedule = bag.get("schedule")
            if not isinstance(schedule, dict):
                continue
            schedule = dict(schedule)

            if forward:
                if "active_hours" not in schedule:
                    continue
                weekly = _weekly_from_active_hours(str(schedule.pop("active_hours") or ""))
                if weekly is not None:
                    # Non sovrascrivere una modalità già scelta a mano.
                    schedule.setdefault("mode", "custom")
                    schedule.setdefault("weekly", weekly)
            else:
                mode = schedule.pop("mode", None)
                weekly = schedule.pop("weekly", None)
                schedule.pop("off_hours_message_once", None)
                schedule.pop("resume_on_open", None)
                schedule.pop("apply_to_automations", None)
                if mode == "custom":
                    schedule["active_hours"] = _active_hours_from_weekly(weekly) or "24/7"
                elif mode is not None:
                    schedule["active_hours"] = "24/7"

            if schedule:
                bag["schedule"] = schedule
            else:
                bag.pop("schedule", None)
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = CAST(:bag AS jsonb) WHERE id = :id"),
                {"bag": json.dumps(bag), "id": row_id},
            )


def _rewrite_locked_keys(conn: sa.Connection, *, forward: bool) -> None:
    """`schedule.active_hours` fra le chiavi bloccate di un template.

    Un lock non va semplicemente buttato: un'agenzia che aveva bloccato gli
    orari voleva impedire ai merchant di cambiarli, e sbloccarli in silenzio
    durante una migrazione ribalterebbe quella decisione. Il lock viene quindi
    *tradotto* sulle chiavi che ora esprimono la stessa cosa.
    """
    rows = conn.execute(
        sa.text(
            "SELECT id, locked_keys FROM bot_templates "
            "WHERE locked_keys IS NOT NULL AND jsonb_typeof(locked_keys) = 'array'"
        )
    ).fetchall()
    old, new = "schedule.active_hours", ("schedule.mode", "schedule.weekly")
    for row_id, keys in rows:
        if not isinstance(keys, list):
            continue
        if forward:
            if old not in keys:
                continue
            out = [k for k in keys if k != old]
            out.extend(k for k in new if k not in out)
        else:
            if not any(k in keys for k in new):
                continue
            out = [k for k in keys if k not in new]
            if old not in out:
                out.append(old)
        conn.execute(
            sa.text("UPDATE bot_templates SET locked_keys = CAST(:keys AS jsonb) WHERE id = :id"),
            {"keys": json.dumps(out), "id": row_id},
        )


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("off_hours_pending_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversations_off_hours_pending",
        "conversations",
        ["off_hours_pending_at"],
        postgresql_where=sa.text("off_hours_pending_at IS NOT NULL"),
    )

    conn = op.get_bind()
    _rewrite_bags(conn, forward=True)
    _rewrite_locked_keys(conn, forward=True)


def downgrade() -> None:
    conn = op.get_bind()
    _rewrite_locked_keys(conn, forward=False)
    _rewrite_bags(conn, forward=False)

    op.drop_index("ix_conversations_off_hours_pending", table_name="conversations")
    op.drop_column("conversations", "off_hours_pending_at")
