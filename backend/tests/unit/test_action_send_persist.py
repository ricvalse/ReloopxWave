"""Fix booking agent-first (blocco B):

- `_format_human` include giorno della settimana e anno, così il modello ha un
  ancoraggio temporale reale sugli slot;
- gli invii degli action handler passano da `send_action_reply`, che INVIA e
  PERSISTE il messaggio (visibile in inbox + presente nella history del turno
  successivo — prima uscivano dal solo transport, invisibili e dimenticati).

Fake sender + fake MessageRepository: niente DB, niente rete.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ai_core.actions import booking
from ai_core.actions.booking import _format_human, send_action_reply
from ai_core.conversation_service import TurnContext


@dataclass
class _FakeSender:
    calls: list[dict] = field(default_factory=list)

    async def send(
        self, *, phone_number_id: str, api_key: str, to_phone: str, text: str, waba_base_url=None
    ) -> str:
        self.calls.append({"to": to_phone, "text": text})
        return "wamid.x"


def _ctx() -> TurnContext:
    return TurnContext(
        tenant_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        lead_phone="393330000000",
        phone_number_id="PN",
        api_key="k",
    )


class _RecordingMsgRepo:
    """Stand-in for MessageRepository that records persist_outbound_message calls."""

    instances: ClassVar[list[_RecordingMsgRepo]] = []

    def __init__(self, session: Any) -> None:
        self.session = session
        self.persisted: list[dict] = []
        _RecordingMsgRepo.instances.append(self)

    async def persist_outbound_message(self, **kw: Any) -> object:
        self.persisted.append(kw)
        return object()


# --- Fix 2: _format_human -----------------------------------------------------


def test_format_human_has_weekday_and_year() -> None:
    # 2026-07-23 è un giovedì.
    assert _format_human("2026-07-23T10:00:00") == "giovedì 23/07/2026 alle 10:00"


def test_format_human_passthrough_on_garbage() -> None:
    assert _format_human("not-a-date") == "not-a-date"


# --- Fix 3: send_action_reply invia E persiste --------------------------------


async def test_send_action_reply_persists_with_session(monkeypatch) -> None:
    monkeypatch.setattr(booking, "MessageRepository", _RecordingMsgRepo)
    _RecordingMsgRepo.instances.clear()
    sender = _FakeSender()
    ctx = _ctx()
    sess = object()

    wamid = await send_action_reply(sender, ctx, "Ecco gli slot", session=sess)

    assert wamid == "wamid.x"
    assert sender.calls == [{"to": "393330000000", "text": "Ecco gli slot"}]
    # Esattamente una riga persistita, forma giusta, sulla sessione passata.
    assert len(_RecordingMsgRepo.instances) == 1
    repo = _RecordingMsgRepo.instances[0]
    assert repo.session is sess
    row = repo.persisted[0]
    assert row["content"] == "Ecco gli slot"
    assert row["conversation_id"] == ctx.conversation_id
    assert row["merchant_id"] == ctx.merchant_id
    assert row["role"] == "agent"
    assert row["wa_message_id"] == "wamid.x"
    assert row["meta"] == {"sender_type": "agent_action"}


async def test_send_action_reply_empty_text_skips_persist(monkeypatch) -> None:
    monkeypatch.setattr(booking, "MessageRepository", _RecordingMsgRepo)
    _RecordingMsgRepo.instances.clear()
    await send_action_reply(_FakeSender(), _ctx(), "", session=object())
    # Nessun MessageRepository costruito: niente bolla vuota in inbox.
    assert _RecordingMsgRepo.instances == []


async def test_send_action_reply_persist_failure_never_breaks_send(monkeypatch) -> None:
    class _Boom:
        def __init__(self, session: Any) -> None:
            pass

        async def persist_outbound_message(self, **kw: Any) -> object:
            raise RuntimeError("db down")

    monkeypatch.setattr(booking, "MessageRepository", _Boom)
    sender = _FakeSender()
    # La persistenza è best-effort: un suo errore NON deve rompere l'invio.
    wamid = await send_action_reply(sender, _ctx(), "ciao", session=object())
    assert wamid == "wamid.x"
    assert sender.calls[0]["text"] == "ciao"
