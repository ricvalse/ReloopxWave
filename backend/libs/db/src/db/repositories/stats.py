"""Query della pagina Statistiche — la sorgente `messages`.

Le bolle si dividono in due famiglie, e la differenza non è cosmetica:

- **strutturali** (messaggi inviati, risposte ricevute, tempo di risposta): il
  loro vocabolario sta nel codice — `direction`, `sender_type`, `automation_id`
  esistono per ogni merchant, per sempre. Nessuna configurazione, nessun
  cablaggio in un'automazione.
- **custom** (un esito dichiarato, es. "ha compilato il questionario"): il
  vocabolario appartiene al merchant, va prima dichiarato e poi cablato in
  un'automazione. Vivono in `repositories/outcome.py`.

Qui c'è la prima famiglia. Una sola primitiva — conta i messaggi che matchano un
filtro strutturale — perché le due bolle principali sono **lo stesso insieme**
letto due volte: gli invii di una campagna, e il sottoinsieme che ha ottenuto
risposta. Che siano lo stesso insieme è ciò che rende il rapporto fra le due un
numero sensato invece di due misure scorrelate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from db.models import Message


@dataclass(slots=True, frozen=True)
class MessageFilter:
    """Filtro strutturale su `messages`. Tutti i campi sono opzionali e in AND."""

    direction: str | None = None  # 'in' | 'out'
    sender_types: tuple[str, ...] = ()
    automation_id: UUID | None = None
    automation_node_key: str | None = None
    profile_id: UUID | None = None
    # True  → solo i messaggi in uscita che hanno ottenuto una risposta
    # False → solo quelli rimasti senza risposta
    # None  → nessun filtro sulla risposta
    has_reply: bool | None = None


@dataclass(slots=True, frozen=True)
class TouchBreakdown:
    """Il funnel di un singolo tocco (nodo) di un'automazione."""

    automation_node_key: str | None
    sent: int
    replied: int


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _apply(
        self,
        stmt: Select[Any],
        *,
        merchant_id: UUID,
        since: datetime,
        f: MessageFilter,
    ) -> Select[Any]:
        stmt = stmt.where(Message.merchant_id == merchant_id, Message.created_at >= since)
        if f.direction:
            stmt = stmt.where(Message.direction == f.direction)
        if f.sender_types:
            stmt = stmt.where(Message.sender_type.in_(list(f.sender_types)))
        if f.automation_id is not None:
            stmt = stmt.where(Message.automation_id == f.automation_id)
        if f.automation_node_key is not None:
            stmt = stmt.where(Message.automation_node_key == f.automation_node_key)
        if f.profile_id is not None:
            stmt = stmt.where(Message.profile_id == f.profile_id)
        if f.has_reply is not None:
            reply = aliased(Message)
            # `reply_to_message_id` è scritto solo sul PRIMO inbound dopo un
            # invio (vedi MessageRepository.resolve_reply_target), quindi questo
            # EXISTS è vero al massimo una volta per messaggio inviato: il
            # conteggio non può superare il numero di invii.
            replied = exists(
                select(reply.id).where(reply.reply_to_message_id == Message.id).correlate(Message)
            )
            stmt = stmt.where(replied if f.has_reply else ~replied)
        return stmt

    async def count_messages(
        self, *, merchant_id: UUID, since: datetime, filters: MessageFilter
    ) -> int:
        stmt = self._apply(
            select(func.count(Message.id)), merchant_id=merchant_id, since=since, f=filters
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def count_distinct_conversations(
        self, *, merchant_id: UUID, since: datetime, filters: MessageFilter
    ) -> int:
        """Come `count_messages` ma conta le conversazioni toccate, non i messaggi.

        È la forma giusta per "quante persone", dove un lead che riceve tre
        messaggi non deve valere tre.
        """
        stmt = self._apply(
            select(func.count(func.distinct(Message.conversation_id))),
            merchant_id=merchant_id,
            since=since,
            f=filters,
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def avg_reply_seconds(
        self, *, merchant_id: UUID, since: datetime, filters: MessageFilter
    ) -> float | None:
        """Tempo medio di risposta, in secondi.

        Arriva gratis dall'attribuzione: è la differenza fra i due `created_at`
        collegati da `reply_to_message_id`. È la ragione principale per cui la
        colonna è un puntatore e non un booleano `replied` — un booleano avrebbe
        buttato via questo dato.
        """
        reply = aliased(Message)
        stmt = select(
            func.avg(func.extract("epoch", reply.created_at - Message.created_at))
        ).join(reply, reply.reply_to_message_id == Message.id)
        stmt = self._apply(stmt, merchant_id=merchant_id, since=since, f=filters)
        value = (await self._session.execute(stmt)).scalar_one_or_none()
        return float(value) if value is not None else None

    async def touch_breakdown(
        self,
        *,
        merchant_id: UUID,
        since: datetime,
        automation_id: UUID,
    ) -> list[TouchBreakdown]:
        """Inviati e risposti per ciascun nodo di un'automazione.

        È la vista che rende leggibile "il DM iniziale converte al 12%, il
        reminder a 7 giorni al 3%" — possibile solo perché `automation_node_key`
        sta sul messaggio e distingue i tocchi fra loro.
        """
        reply = aliased(Message)
        replied_flag = exists(
            select(reply.id).where(reply.reply_to_message_id == Message.id).correlate(Message)
        )
        stmt = (
            select(
                Message.automation_node_key,
                func.count(Message.id),
                func.count(Message.id).filter(replied_flag),
            )
            .where(
                Message.merchant_id == merchant_id,
                Message.created_at >= since,
                Message.direction == "out",
                Message.automation_id == automation_id,
            )
            .group_by(Message.automation_node_key)
            .order_by(Message.automation_node_key)
        )
        rows = (await self._session.execute(stmt)).tuples().all()
        return [
            TouchBreakdown(
                automation_node_key=row[0],
                sent=int(row[1] or 0),
                replied=int(row[2] or 0),
            )
            for row in rows
        ]

    async def list_automations_with_traffic(
        self, *, merchant_id: UUID, since: datetime
    ) -> list[UUID]:
        """Le automazioni che hanno effettivamente inviato qualcosa nella finestra.

        Popola il selettore della pagina Statistiche senza offrire campagne mute.
        """
        stmt = (
            select(Message.automation_id)
            .where(
                Message.merchant_id == merchant_id,
                Message.created_at >= since,
                Message.automation_id.isnot(None),
            )
            .group_by(Message.automation_id)
            .order_by(func.count(Message.id).desc())
        )
        return [row for row in (await self._session.execute(stmt)).scalars() if row is not None]


__all__ = ["MessageFilter", "StatsRepository", "TouchBreakdown"]
