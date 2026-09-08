"""Proposta di appuntamento al lead caldo (`booking.propose_when_hot`).

Due metà, testate separatamente perché rispondono a due domande diverse:

- `_booking_nudge_block` sceglie il *testo*, e l'unica cosa che lo governa è se
  questo turno può davvero leggere il calendario. È qui che si evita il vicolo
  cieco documentato in `render_schema_hint`: promettere orari veri quando il loop
  dei tool non gira lascia il cliente con "un attimo che verifico" e nient'altro.
- `_resolve_booking_nudge` decide *se* spingere, e raccoglie i cancelli che
  dipendono dallo stato del lead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from ai_core.conversation_service import (
    _NO_BOOKING_NUDGE_STATES,
    ConversationService,
    _ReplyContext,
)
from ai_core.orchestrator import ConversationContext, _booking_nudge_block
from ai_core.state_machine import ConvState
from config_resolver.schema import ConfigKey


def _ctx(**kw: Any) -> ConversationContext:
    base: dict[str, Any] = {
        "merchant_id": uuid4(),
        "tenant_id": uuid4(),
        "lead_id": uuid4(),
        "lead_score": 85,
        "hot_threshold": 80,
        "system_prompt": "persona",
        "propose_booking": True,
    }
    base.update(kw)
    return ConversationContext(**base)


# --------------------------------------------------------------------------
# Il testo della direttiva
# --------------------------------------------------------------------------


def test_con_i_tool_chiede_orari_reali() -> None:
    text = _booking_nudge_block(_ctx(), tools_available=True)
    assert "check_availability" in text
    assert "Non inventare orari" in text


def test_senza_tool_non_promette_orari() -> None:
    """Il caso che rende la feature sicura invece che dannosa.

    Senza loop dei tool il modello non può leggere il calendario: se gli
    ordinassimo comunque di verificare, scriverebbe la frase d'attesa e il
    follow-up non arriverebbe mai (`orchestrator.py`, warning
    `tool_call_dropped`). Deve invece chiedere una fascia.
    """
    text = _booking_nudge_block(_ctx(), tools_available=False)
    assert "check_availability" not in text
    assert "NON citare orari specifici" in text
    assert "fascia oraria preferisce" in text


def test_allowlist_senza_check_availability_degrada() -> None:
    """Un playbook che permette `book_slot` ma non `check_availability` passa il
    gate del servizio: se non degradassimo qui, sarebbe lo stesso vicolo cieco."""
    text = _booking_nudge_block(_ctx(allowed_actions={"book_slot"}), tools_available=True)
    assert "check_availability" not in text
    assert "NON citare orari specifici" in text


def test_allowlist_con_check_availability_promette_orari() -> None:
    text = _booking_nudge_block(
        _ctx(allowed_actions={"book_slot", "check_availability"}), tools_available=True
    )
    assert "check_availability" in text


def test_direttiva_chiede_di_rispondere_prima() -> None:
    """Il turno resta del cliente: senza questo il bot risponde a una domanda
    con un orario."""
    text = _booking_nudge_block(_ctx(), tools_available=True)
    assert "Prima rispondi a quello che ha appena chiesto" in text


def test_direttiva_frena_l_insistenza() -> None:
    text = _booking_nudge_block(_ctx(), tools_available=True)
    assert "una volta sola" in text
    assert "già un appuntamento" in text


def test_istruzioni_del_merchant_in_coda() -> None:
    text = _booking_nudge_block(
        _ctx(propose_instructions="Solo la sede di Milano."), tools_available=True
    )
    assert text.rstrip().endswith("Solo la sede di Milano.")


def test_prompt_riporta_punteggio_e_soglia_del_merchant() -> None:
    text = _booking_nudge_block(_ctx(lead_score=91, hot_threshold=70), tools_available=True)
    assert "91/100" in text
    assert "soglia 70" in text


def test_niente_blocco_quando_non_eleggibile() -> None:
    """`propose_booking=False` non deve lasciare traccia nel system prompt."""
    svc_ctx = _ctx(propose_booking=False)
    from ai_core.orchestrator import ConversationOrchestrator

    messages = ConversationOrchestrator.__dict__["_build_messages"](
        object.__new__(ConversationOrchestrator), svc_ctx, "ciao", tools_available=True
    )
    assert "OBIETTIVO DI QUESTO TURNO" not in messages[0].content


def test_blocco_presente_e_in_append_al_blocco_qualificazione() -> None:
    """La proposta si aggiunge a `move_pipeline`, non lo sostituisce: sono due
    cose diverse e sostituire spegnerebbe l'avanzamento sui lead migliori."""
    from ai_core.orchestrator import ConversationOrchestrator

    messages = ConversationOrchestrator.__dict__["_build_messages"](
        object.__new__(ConversationOrchestrator), _ctx(), "ciao", tools_available=True
    )
    system = messages[0].content
    assert "OBIETTIVO DI QUESTO TURNO" in system
    assert "Emetti `move_pipeline`" in system


# --------------------------------------------------------------------------
# Il gate di eleggibilità
# --------------------------------------------------------------------------


@dataclass
class _Caps:
    scoring_enabled: bool = True
    booking_enabled: bool = True


def _rc(score: int = 85, meta: dict[str, Any] | None = None) -> _ReplyContext:
    rc = object.__new__(_ReplyContext)
    object.__setattr__(rc, "lead_score", score)
    object.__setattr__(rc, "conv_meta", meta if meta is not None else {})
    return rc


class _Svc:
    """Solo i due metodi che servono: il gate e il resolver che interroga."""

    _resolve_booking_nudge = ConversationService._resolve_booking_nudge

    def __init__(self, max_per_conv: int = 1) -> None:
        self._max = max_per_conv

    async def _resolve_int(
        self, session: Any, merchant_id: Any, key: ConfigKey, *, default: int
    ) -> int:
        assert key is ConfigKey.BOOKING_PROPOSE_MAX_PER_CONVERSATION
        return self._max


async def _gate(
    *,
    enabled: bool = True,
    caps: _Caps | None = None,
    score: int = 85,
    hot: int = 80,
    state: ConvState = ConvState.PITCHING,
    meta: dict[str, Any] | None = None,
    max_per_conv: int = 1,
) -> bool:
    return await _Svc(max_per_conv)._resolve_booking_nudge(  # type: ignore[arg-type]
        None,
        _rc(score, meta),
        caps=caps or _Caps(),
        merchant_id=uuid4(),
        enabled=enabled,
        fsm_state=state,
        hot_threshold=hot,
    )


@pytest.mark.asyncio
async def test_lead_caldo_viene_spinto() -> None:
    assert await _gate() is True


@pytest.mark.asyncio
async def test_spento_di_default_non_spinge() -> None:
    """ADR 0014: niente feature accese di default."""
    assert await _gate(enabled=False) is False


@pytest.mark.asyncio
async def test_sotto_soglia_non_spinge() -> None:
    assert await _gate(score=79, hot=80) is False


@pytest.mark.asyncio
async def test_sulla_soglia_spinge() -> None:
    assert await _gate(score=80, hot=80) is True


@pytest.mark.asyncio
async def test_scoring_spento_non_spinge() -> None:
    """Senza scoring il punteggio non è mantenuto: 'caldo' non vuol dire nulla."""
    assert await _gate(caps=_Caps(scoring_enabled=False)) is False


@pytest.mark.asyncio
async def test_booking_spento_non_spinge() -> None:
    """Le azioni di prenotazione non sono nemmeno nello schema: sarebbe un
    vicolo cieco chiederle."""
    assert await _gate(caps=_Caps(booking_enabled=False)) is False


@pytest.mark.parametrize("state", sorted(_NO_BOOKING_NUDGE_STATES, key=lambda s: s.value))
@pytest.mark.asyncio
async def test_stati_terminali_non_spingono(state: ConvState) -> None:
    """BOOKED è il caso che conta: `actions/booking.py` forza il punteggio a 100
    dopo la prenotazione, quindi senza questo cancello chi ha già un appuntamento
    se lo vedrebbe riproporre per sempre."""
    assert await _gate(state=state) is False


@pytest.mark.asyncio
async def test_tetto_esaurito_non_spinge() -> None:
    assert await _gate(meta={"booking_nudge_count": 1}, max_per_conv=1) is False


@pytest.mark.asyncio
async def test_tetto_alzato_concede_un_secondo_turno() -> None:
    assert await _gate(meta={"booking_nudge_count": 1}, max_per_conv=2) is True


@pytest.mark.asyncio
async def test_contatore_corrotto_non_esplode() -> None:
    """`meta` è JSONB scritto da più percorsi: un valore non-intero non deve
    far saltare il turno."""
    assert await _gate(meta={"booking_nudge_count": "molte"}) is True


@pytest.mark.asyncio
async def test_meta_vuoto_e_il_caso_normale() -> None:
    assert await _gate(meta={}) is True


# --------------------------------------------------------------------------
# Parità col playground (ADR 0009)
# --------------------------------------------------------------------------


def test_il_playground_passa_la_proposta_al_contesto() -> None:
    """Il playground costruisce il proprio `ConversationContext`: se dimentica
    `propose_booking`, il merchant accende l'interruttore, non vede cambiare
    nulla nell'anteprima, e la prima verifica reale finisce su un cliente vero.

    Guardia strutturale — verifica che il campo sia cablato, senza istanziare
    l'intero servizio (che vorrebbe DB, embedder e LLM).
    """
    import inspect

    from ai_core import playground

    src = inspect.getsource(playground)
    assert "propose_booking=propose_booking" in src
    assert "ConfigKey.BOOKING_PROPOSE_WHEN_HOT" in src
    assert "ConfigKey.BOOKING_PROPOSE_INSTRUCTIONS" in src
