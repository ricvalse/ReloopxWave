"""ADR 0031 — "il bot risponde solo dove è passata un'automazione".

Due livelli, perché il gate vive in due punti:

1. il predicato condiviso `_automation_scope_blocks`, che è dove sta la logica;
2. il secondo gate, quello di `generate_and_send_reply`, che è la porta da cui
   entrano il flush del debounce **e** lo sweep di ripresa dopo gli orari.

Il primo gate (turno in ingresso) è coperto in `test_uc01_conversation_service.py`,
dove vive l'impalcatura di `handle_inbound`.

Perché il secondo gate ha un test suo: `resume_after_hours` chiama
`generate_and_send_reply` direttamente, saltando del tutto la fase 1. Una
modalità applicata solo nel primo punto lascerebbe passare esattamente le
conversazioni rimaste in sospeso una notte — cioè quelle in cui il merchant si
accorge di meno che il bot ha parlato a chi non doveva.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from ai_core.conversation_service import ConversationService
from db import ResolvedWhatsAppIntegration

pytestmark = pytest.mark.asyncio


class _OltreIlGateError(Exception):
    """Alzata appena il codice supera il gate: segna il confine da osservare."""


@dataclass
class _Conv:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    auto_reply: bool = True
    ai_disabled_until: Any = None
    last_automation_at: Any = None


@dataclass
class _Lead:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    opted_out_at: Any = None


class _Svc(ConversationService):
    """Solo il predicato: nessun collaboratore serve per interrogarlo."""

    def __init__(self, scope: str | None, *, esplode: bool = False) -> None:
        self._scope = scope
        self._esplode = esplode

    async def _resolve_optional_str(self, session, merchant_id, key):  # type: ignore[override]
        # Rispecchia il vero: qualunque errore di risoluzione degrada a None,
        # non propaga.
        if self._esplode:
            return None
        return self._scope


# ---- il predicato ---------------------------------------------------------


async def test_scope_tutti_non_blocca_mai() -> None:
    conv = _Conv(last_automation_at=None)
    assert await _Svc("tutti")._automation_scope_blocks(object(), uuid.uuid4(), conv) is False


async def test_scope_assente_vale_tutti() -> None:
    """Il default della cascata, e insieme il comportamento in caso di guasto."""
    conv = _Conv(last_automation_at=None)
    assert await _Svc(None)._automation_scope_blocks(object(), uuid.uuid4(), conv) is False


async def test_config_irraggiungibile_fa_passare() -> None:
    """Fail-open: Redis giù non deve far ammutolire il bot ovunque insieme."""
    conv = _Conv(last_automation_at=None)
    svc = _Svc("solo_automazioni", esplode=True)
    assert await svc._automation_scope_blocks(object(), uuid.uuid4(), conv) is False


async def test_solo_automazioni_blocca_il_contatto_a_freddo() -> None:
    conv = _Conv(last_automation_at=None)
    svc = _Svc("solo_automazioni")
    assert await svc._automation_scope_blocks(object(), uuid.uuid4(), conv) is True


async def test_solo_automazioni_lascia_passare_dove_c_e_il_timbro() -> None:
    conv = _Conv(last_automation_at=datetime(2020, 1, 1, tzinfo=UTC))
    svc = _Svc("solo_automazioni")
    # Timbro del 2020: il permesso non scade. È un latch, non una finestra.
    assert await svc._automation_scope_blocks(object(), uuid.uuid4(), conv) is False


# ---- il secondo gate: flush del debounce e ripresa dopo gli orari ----------


def _harness(
    monkeypatch: pytest.MonkeyPatch, *, conv: _Conv, scope: str
) -> tuple[ConversationService, list]:
    from ai_core import conversation_service as cs

    integration = ResolvedWhatsAppIntegration(
        tenant_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        phone_number_id="PNID-1",
        api_key="k",
        waba_base_url=None,
        meta={},
    )
    lead = _Lead()
    inviati: list = []

    async def fake_resolve(self, phone_number_id):
        return integration

    async def fake_bool(self, session, merchant_id, key, *, default):
        return True  # master switch acceso

    async def fake_scope(self, session, merchant_id, key):
        return scope

    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return conv

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def upsert_by_phone(self, *, merchant_id, phone, campaign=None):
            return lead

    class FakeMsgRepo:
        def __init__(self, session): ...
        async def list_history(self, conversation_id, *, limit=30):
            # Primo passo dopo il gate. Alzare qui rende il confine osservabile
            # senza dover finire di simulare tutto il turno.
            inviati.append("history")
            raise _OltreIlGateError

    monkeypatch.setattr(cs.ConversationService, "_resolve_integration", fake_resolve)
    monkeypatch.setattr(cs.ConversationService, "_resolve_bool", fake_bool)
    monkeypatch.setattr(cs.ConversationService, "_resolve_optional_str", fake_scope)
    monkeypatch.setattr(cs, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(cs, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(cs, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(cs, "MessageRepository", FakeMsgRepo)

    svc = ConversationService(
        orchestrator=None,
        action_dispatcher=None,
        reply_sender=None,
        embedder=None,
        kek_base64="unused",
    )
    return svc, inviati


async def test_il_flush_non_risponde_a_freddo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il caso che il primo gate da solo non copre.

    `resume_after_hours` entra da qui saltando la fase 1: senza il controllo
    anche in questo punto, la domanda arrivata di notte da un contatto a freddo
    riceverebbe risposta alla riapertura.
    """
    conv = _Conv(last_automation_at=None)
    svc, oltre_il_gate = _harness(monkeypatch, conv=conv, scope="solo_automazioni")

    result = await svc.generate_and_send_reply(
        phone_number_id="PNID-1",
        from_phone="39333",
        text="ciao",
        wa_message_id="wamid.x",
        resumed_after_hours=True,
    )

    assert result.handled is False
    assert result.reason == "no_automation"
    assert oltre_il_gate == []  # fermato prima di caricare la storia


async def test_il_flush_risponde_se_l_automazione_e_arrivata_nel_frattempo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lo stato va riletto: durante l'attesa può essere partita una campagna.

    Il gate non si limita a fidarsi della decisione presa quando il messaggio è
    arrivato — rilegge la conversazione. Se nel frattempo un'automazione ha
    scritto su questo thread, adesso la risposta è dovuta.
    """
    conv = _Conv(last_automation_at=datetime.now(UTC))
    svc, oltre_il_gate = _harness(monkeypatch, conv=conv, scope="solo_automazioni")

    with pytest.raises(_OltreIlGateError):
        await svc.generate_and_send_reply(
            phone_number_id="PNID-1",
            from_phone="39333",
            text="ciao",
            wa_message_id="wamid.x",
        )

    assert oltre_il_gate == ["history"]
