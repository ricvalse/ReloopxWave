from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, Message


def _meta_with_sender(sender_type: str, meta: dict[str, Any] | None) -> dict[str, Any]:
    """Merge `meta` keeping `sender_type` mirrored inside the JSONB.

    Da 0047 `sender_type` è una colonna, ed è quella la fonte di verità. La copia
    dentro `meta` resta scritta di proposito: il portale merchant legge i
    messaggi **direttamente da Supabase** (le due vie di accesso ai dati della
    spec 4.4) e in Realtime, quindi durante la finestra di deploy un frontend
    ancora vecchio leggerebbe `meta.sender_type`. Da rimuovere quando il
    frontend in produzione legge la colonna.
    """
    merged: dict[str, Any] = {"sender_type": sender_type}
    if meta:
        merged.update(meta)
    merged["sender_type"] = sender_type
    return merged


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_wa_message_id(self, wa_message_id: str) -> Message | None:
        """Return the message with this WhatsApp id, or None.

        Used to make inbound persistence idempotent: the WA webhook may be
        re-delivered and the worker job re-run, so we skip the insert when the
        message is already stored. `wa_message_id` is indexed for this lookup.
        """
        stmt = select(Message).where(Message.wa_message_id == wa_message_id).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def list_history(self, conversation_id: UUID, *, limit: int = 30) -> list[Message]:
        """Returns the last `limit` messages in chronological (oldest-first) order."""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        rows = list((await self._session.execute(stmt)).scalars())
        rows.reverse()
        return rows

    async def resolve_reply_target(self, conversation_id: UUID) -> Message | None:
        """L'outbound a cui un inbound in arrivo *adesso* sta rispondendo, se c'è.

        Attribuzione **last-touch**: vale l'ultimo messaggio in uscita, e solo se
        nessun inbound lo ha già seguito. Cioè: se l'ultimo messaggio della
        conversazione è in uscita, questo inbound è la **prima** risposta a quel
        tocco e glielo attribuiamo; se l'ultimo è già in entrata, il lead sta
        proseguendo e non attribuiamo niente.

        Perché "solo la prima": senza questa condizione un lead che scrive tre
        messaggi di fila conterebbe tre risposte per un solo invio, e il
        reply-rate supererebbe il 100%. Con questa regola `COUNT` sugli inbound
        attribuiti è già il numero di tocchi che hanno ottenuto una risposta,
        senza DISTINCT.

        Una sola query, servita da `ix_messages_conv_created`.
        """
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last = (await self._session.execute(stmt)).scalars().first()
        if last is None or last.direction != "out":
            return None
        return last

    async def persist_user_message(
        self,
        *,
        conversation_id: UUID,
        merchant_id: UUID,
        content: str,
        wa_message_id: str | None,
        variant_id: str | None = None,
        meta: dict[str, Any] | None = None,
        reply_to_message_id: UUID | None = None,
        profile_id: UUID | None = None,
    ) -> Message:
        # `sender_type='customer'` lets the inbox distinguish inbound customer
        # turns from system-synthesized ones; `meta.media` carries the
        # attachment descriptor (storage_path filled once the download lands).
        msg = Message(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            role="user",
            direction="in",
            sender_type="customer",
            content=content,
            wa_message_id=wa_message_id,
            variant_id=variant_id,
            # Scritto una sola volta, all'INSERT: è ciò che rende il reply-rate
            # un JOIN invece di una UPDATE retroattiva su una riga passata.
            reply_to_message_id=reply_to_message_id,
            profile_id=profile_id,
            meta=_meta_with_sender("customer", meta),
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def patch_message_media(
        self,
        *,
        wa_message_id: str,
        merchant_id: UUID,
        media_patch: dict[str, Any],
    ) -> None:
        """Merge `media_patch` into `meta -> 'media'` for a message, by wa id.

        Used by the worker after a media download lands (or fails) to fill
        `storage_path` / `size_bytes` / `transcription` / `error` on the row
        inserted during phase-1 persist. Scoped by `merchant_id` (RLS already
        constrains the session, but keep the guard explicit — the worker writes
        service-role). No-op if the row or its wa id is missing.
        """
        import json

        await self._session.execute(
            text(
                "UPDATE messages SET meta = jsonb_set("
                "COALESCE(meta, '{}'::jsonb), '{media}', "
                "COALESCE(meta -> 'media', '{}'::jsonb) || CAST(:patch AS jsonb)) "
                "WHERE wa_message_id = :wa_id AND merchant_id = :mid"
            ),
            {"patch": json.dumps(media_patch), "wa_id": wa_message_id, "mid": str(merchant_id)},
        )

    async def persist_phone_echo_message(
        self,
        *,
        conversation_id: UUID,
        merchant_id: UUID,
        content: str,
        wa_message_id: str,
        meta: dict[str, Any] | None = None,
    ) -> Message:
        """Outbound message that originated from the merchant's phone Business App.

        Stored as `role='agent', direction='out', status='sent'` — the message has
        already been delivered by the time we hear about it, so we skip the
        pending→sent state machine entirely. `sender_type='phone'` lets the UI
        distinguish phone-typed replies from composer-typed ones; `meta.media`
        carries an attachment descriptor for a photo sent from the handset.
        """
        msg = Message(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            role="agent",
            direction="out",
            status="sent",
            sender_type="phone",
            content=content,
            wa_message_id=wa_message_id,
            meta=_meta_with_sender("phone", meta),
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def persist_outbound_message(
        self,
        *,
        conversation_id: UUID,
        merchant_id: UUID,
        content: str,
        wa_message_id: str | None,
        role: str = "agent",
        status: str = "sent",
        sender_type: str = "automation",
        automation_id: UUID | None = None,
        automation_node_key: str | None = None,
        profile_id: UUID | None = None,
        meta: dict[str, object] | None = None,
    ) -> Message:
        """Persist a proactive outbound message (scheduler/automation send).

        Mirrors the bot-reply/composer pipeline so a row exists in the inbox and
        so `update_outbound_status` can attach delivery callbacks via
        `wa_message_id` (otherwise the callback is dropped as `row_missing`).
        Defaults to `role='agent'`; pass `role='system'` for system-driven copy.

        `automation_id` / `automation_node_key` sono l'attribuzione: senza di
        loro "quanti messaggi ha mandato questa campagna" non è una domanda
        rispondibile, né dagli eventi né dai messaggi. Il `node_key` distingue i
        tocchi fra loro (il DM iniziale dal reminder a 7 giorni).

        Also bumps the conversation's `last_message_at`/`message_count` (same
        semantics as `ConversationRepository.touch_last_message`): the inbox rail
        is ordered by `last_message_at`, so without the bump a proactive send
        never surfaces the thread — and an automation-opened conversation
        (`last_message_at` NULL) sorts to the very bottom.
        """
        msg = Message(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            role=role,
            direction="out",
            status=status,
            sender_type=sender_type,
            content=content,
            wa_message_id=wa_message_id,
            automation_id=automation_id,
            automation_node_key=automation_node_key,
            profile_id=profile_id,
            meta=_meta_with_sender(sender_type, dict(meta) if meta else None),
        )
        self._session.add(msg)
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                last_message_at=datetime.now(tz=UTC),
                message_count=Conversation.message_count + 1,
            )
        )
        await self._session.flush()
        return msg

    async def persist_assistant_message(
        self,
        *,
        conversation_id: UUID,
        merchant_id: UUID,
        content: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        variant_id: str | None = None,
        profile_id: UUID | None = None,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            merchant_id=merchant_id,
            role="assistant",
            direction="out",
            sender_type="ai",
            content=content,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            variant_id=variant_id,
            # Con quale profilo è stata generata questa risposta: è il timbro che
            # permette di leggere le statistiche "per profilo" anche sui turni
            # conversazionali, non solo sugli invii proattivi.
            profile_id=profile_id,
            meta={"sender_type": "ai"},
        )
        self._session.add(msg)
        await self._session.flush()
        return msg
