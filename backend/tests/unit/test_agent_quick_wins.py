"""Quick wins portati da Amalia sul motore agente (audit 2026-08-05).

Coprono quattro seam che prima non avevano alcun test:
  1. `_parse_structured` con input malformato — il JSON grezzo non deve MAI
     diventare il messaggio inviato al cliente (né finire in `messages.content`,
     e quindi in inbox, nella history del turno dopo e nel dataset di FT).
  4/8. Blocco di stile WhatsApp + lock anti-drift in coda al system prompt.
  7. Gli strumenti di lettura non vengono annunciati quando nessun loop può
     eseguirli (vicolo cieco deterministico).
"""

from __future__ import annotations

import json
import uuid

from ai_core.llm import ChatMessage, CompletionResult
from ai_core.orchestrator import (
    ConversationContext,
    ConversationOrchestrator,
    _parse_structured,
    render_schema_hint,
)


# --------------------------------------------------------------------------- #
# FIX 1 — mai il JSON grezzo al cliente
# --------------------------------------------------------------------------- #
def test_valid_structured_response_is_parsed() -> None:
    parsed = _parse_structured(
        json.dumps({"reply_text": "Ciao!", "actions": [{"kind": "none", "payload": {}}]})
    )
    assert parsed.reply_text == "Ciao!"
    assert [a.kind for a in parsed.actions] == ["none"]


def test_prose_is_preserved_verbatim() -> None:
    """La prosa è una forma LEGITTIMA: il fallback Anthropic non riceve mai
    `response_format`. Non va toccata."""
    parsed = _parse_structured("Certo, ti richiamo domani mattina.")
    assert parsed.reply_text == "Certo, ti richiamo domani mattina."
    assert parsed.actions == []


def test_invented_action_kind_keeps_the_text_and_drops_the_actions() -> None:
    """Un solo `kind` fuori enum fa fallire l'INTERO model_validate_json: prima
    si perdeva anche un reply_text perfettamente buono, e al suo posto partiva
    il blob."""
    raw = json.dumps({"reply_text": "Ti confermo a breve.", "actions": [{"kind": "send_sms"}]})
    parsed = _parse_structured(raw)
    assert parsed.reply_text == "Ti confermo a breve."
    assert parsed.actions == []


def test_null_actions_keeps_the_text() -> None:
    parsed = _parse_structured('{"reply_text": "Eccomi", "actions": null}')
    assert parsed.reply_text == "Eccomi"


def test_json_fence_is_unwrapped() -> None:
    raw = '```json\n{"reply_text": "Ciao", "actions": [{"kind": "nope"}]}\n```'
    assert _parse_structured(raw).reply_text == "Ciao"


def test_conformant_fenced_payload_keeps_its_actions() -> None:
    """Il fence non deve costare le azioni: il primo tentativo Pydantic gira sul
    raw non de-fenced e fallisce sempre, quindi va ritentato sul candidate."""
    raw = (
        "```json\n"
        '{"reply_text": "Procedo subito", "actions": [{"kind": "book_slot", "payload": {}}]}'
        "\n```"
    )
    parsed = _parse_structured(raw)
    assert parsed.reply_text == "Procedo subito"
    assert [a.kind for a in parsed.actions] == ["book_slot"]


def test_one_invented_kind_does_not_kill_the_valid_actions() -> None:
    """Pydantic aborta l'intera lista al primo elemento non valido: un
    `book_slot` legittimo spariva insieme al `kind` inventato, e il cliente
    leggeva la frase di passaggio per un appuntamento mai creato."""
    raw = json.dumps(
        {
            "reply_text": "Procedo subito e ti confermo",
            "actions": [{"kind": "send_email"}, {"kind": "book_slot", "payload": {"x": 1}}],
        }
    )
    parsed = _parse_structured(raw)
    assert [a.kind for a in parsed.actions] == ["book_slot"]


def test_handoff_survives_the_rename_end_to_end() -> None:
    """ADR 0026 rinomina `escalate_human` in `handoff_human` e normalizza subito
    dopo il parse, ma i consumatori confrontavano ancora il nome storico: dopo la
    normalizzazione nessuno riconosceva più l'azione, `claim_handoff` non partiva
    e il dispatcher scartava l'handler in silenzio. Il rename era coperto solo da
    test sullo schema hint, mai sul percorso di dispatch — questo lo copre."""
    from ai_core.actions.escalate import EscalateHumanHandler
    from ai_core.conversation_service import _HANDOFF_ACTION_KINDS
    from ai_core.orchestrator import normalize_action_kind

    for emitted in ("escalate_human", "handoff_human"):
        raw = json.dumps({"reply_text": "Ti passo un collega", "actions": [{"kind": emitted}]})
        kinds = [a.kind for a in _parse_structured(raw).actions]
        assert kinds == ["handoff_human"], f"{emitted} non normalizzato"
        # la handoff policy deve riconoscerla...
        assert kinds[0] in _HANDOFF_ACTION_KINDS
        # ...e il dispatcher deve avere una chiave che la raccoglie
        registered = {
            EscalateHumanHandler.kind,
            normalize_action_kind(EscalateHumanHandler.kind),
        }
        assert kinds[0] in registered


def test_bare_json_string_is_treated_as_prose() -> None:
    """Il modello che risponde con una stringa quotata sta rispondendo in prosa
    che per caso è JSON valido: `json.loads` riesce, quindi il ramo prosa non
    veniva mai raggiunto e la risposta buona finiva scartata."""
    assert _parse_structured('"Ciao Marco, certo!"').reply_text == '"Ciao Marco, certo!"'


def test_bare_json_number_is_treated_as_prose() -> None:
    assert _parse_structured("450").reply_text == "450"


def test_prose_opening_with_a_bracket_is_not_mistaken_for_json() -> None:
    """Il prompt insegna al modello a scrivere note fra parentesi quadre
    (`[Il cliente ha inviato un'immagine]`), quindi una prosa che inizia con `[`
    è plausibile e non va scambiata per un artefatto."""
    raw = "[Nota: non riesco a vedere la foto] Puoi descrivermela a parole?"
    assert _parse_structured(raw).reply_text == raw


def test_json_without_usable_text_never_leaks_the_blob() -> None:
    """Il caso che il fix esiste per fermare: JSON valido, schema sbagliato,
    nessun testo recuperabile. Deve uscire vuoto (→ fail-safe del chiamante),
    mai il blob."""
    raw = json.dumps({"actions": [{"kind": "none"}], "confidence": 0.9})
    parsed = _parse_structured(raw)
    assert parsed.reply_text == ""
    assert raw not in parsed.reply_text


def test_truncated_json_never_leaks_the_blob() -> None:
    raw = '{"reply_text": "Sto verificando la disponi'
    assert _parse_structured(raw).reply_text != raw


def test_empty_reply_text_in_valid_schema_stays_empty() -> None:
    parsed = _parse_structured('{"reply_text": "   ", "actions": []}')
    assert not parsed.reply_text.strip()


# --------------------------------------------------------------------------- #
# FIX 7 — non annunciare strumenti che nessuno eseguirà
# --------------------------------------------------------------------------- #
def test_hint_hides_read_tools_when_no_loop_can_run() -> None:
    hint = render_schema_hint(None, tools_available=False)
    assert "STRUMENTI DI LETTURA" not in hint
    assert "check_availability" not in hint
    assert "lookup_appointment" not in hint
    # le azioni con effetti restano
    assert "book_slot" in hint


def test_hint_default_still_advertises_the_tools() -> None:
    """Default invariato: il worker ha l'executor e il loop gira davvero."""
    hint = render_schema_hint(None)
    assert "STRUMENTI DI LETTURA" in hint
    assert "check_availability" in hint
    assert hint == render_schema_hint(None, tools_available=True)


def test_booking_note_keeps_its_closing_tool_sentence_by_default() -> None:
    """La nota anti-false-conferme è stata spezzata in due per poter nascondere
    la frase che punta a check_availability. Questo pinna la ricomposizione: il
    golden test storico è auto-referenziale e non se ne accorgerebbe."""
    assert render_schema_hint(None).rstrip().endswith("usa check_availability.")


def test_booking_note_without_tools_does_not_point_at_them() -> None:
    hint = render_schema_hint(None, tools_available=False)
    assert "niente false conferme" in hint
    assert "usa check_availability" not in hint


class _FakeClient:
    model = "fake-model"

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[list[ChatMessage]] = []
        self.temperatures: list[float] = []

    async def complete(self, *, messages, response_format=None, temperature=0.3, max_tokens=None):
        self.calls.append(list(messages))
        self.temperatures.append(temperature)
        return CompletionResult(
            content=self._content, model=self.model, tokens_in=1, tokens_out=1, latency_ms=1, raw={}
        )


class _FakeRouter:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def select(self, req):
        return self._client

    async def fallback(self):
        return None


def _ctx(**overrides) -> ConversationContext:
    base = {
        "merchant_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "lead_id": None,
        "lead_score": 0,
        "hot_threshold": 80,
        "system_prompt": "PERSONA",
    }
    base.update(overrides)
    return ConversationContext(**base)


async def test_run_without_executor_does_not_advertise_tools() -> None:
    """Il playground chiama `run` senza executor: prima il prompt gli prometteva
    strumenti che non sarebbero mai partiti."""
    client = _FakeClient('{"reply_text": "ok", "actions": []}')
    orch = ConversationOrchestrator(router=_FakeRouter(client))
    await orch.run(_ctx(), "quando siete liberi?")
    assert "STRUMENTI DI LETTURA" not in client.calls[0][0].content


async def test_run_with_executor_and_iterations_advertises_tools() -> None:
    class _Exec:
        async def execute_read(self, action, ctx):  # pragma: no cover - non raggiunto
            raise AssertionError("il modello non ha chiesto strumenti")

    client = _FakeClient('{"reply_text": "ok", "actions": []}')
    orch = ConversationOrchestrator(router=_FakeRouter(client))
    await orch.run(_ctx(), "ciao", tool_executor=_Exec(), max_iterations=3)
    assert "STRUMENTI DI LETTURA" in client.calls[0][0].content


async def test_turn_passes_an_explicit_temperature_and_no_token_cap() -> None:
    """FIX 3: il cap è deliberatamente assente — su gpt-5 diventerebbe
    `max_completion_tokens` (reasoning incluso) e troncherebbe il JSON."""
    client = _FakeClient('{"reply_text": "ok", "actions": []}')
    orch = ConversationOrchestrator(router=_FakeRouter(client))
    await orch.run(_ctx(), "ciao")
    assert client.temperatures == [0.3]


# --------------------------------------------------------------------------- #
# FIX 4 / 8 — stile WhatsApp + lock anti-drift, in coda
# --------------------------------------------------------------------------- #
def _system_text(ctx: ConversationContext) -> str:
    orch = ConversationOrchestrator(router=None)
    return orch._build_messages(ctx, "ciao")[0].content


def test_style_block_is_always_present() -> None:
    text = _system_text(_ctx())
    assert "COME SCRIVERE" in text
    assert "IGNORA LO STILE DEI MESSAGGI PRECEDENTI" in text


def test_style_block_scopes_the_only_rule_to_reply_text() -> None:
    """«Rispondi SOLO con il messaggio» contraddiceva l'involucro JSON che lo
    schema impone: va ancorato al campo, non al turno."""
    text = _system_text(_ctx())
    assert "In `reply_text` metti SOLO il testo" in text


def test_style_block_does_not_claim_whatsapp_ignores_markdown() -> None:
    """WhatsApp rende *grassetto*, _corsivo_ e i blocchi di codice: dirlo falso
    nel prompt è peggio che tacere, perché il modello ci generalizza sopra."""
    text = _system_text(_ctx())
    assert "non lo interpreta" not in text
    assert "blocco di codice" not in text
    # resta il divieto sulle sintassi davvero non supportate
    assert "[testo](url)" in text


def test_style_block_does_not_override_verbosity() -> None:
    """«Messaggi brevi» come regola fissa annullava bot.verbosity=dettagliato."""
    assert "Messaggi brevi" not in _system_text(_ctx())


def test_style_lock_is_the_last_thing_the_model_reads() -> None:
    """Deve stare DOPO le direttive del playbook: è l'istruzione più vicina alla
    history che ha il compito di scavalcare."""
    text = _system_text(_ctx(directives=("Mai intervistare il candidato.",)))
    assert text.index("REGOLE DELLA CONVERSAZIONE") < text.index("IGNORA LO STILE")


def test_assistant_name_is_claimed_when_configured() -> None:
    text = _system_text(_ctx(assistant_name="Giulia"))
    assert "Il tuo nome è Giulia" in text


def test_no_name_claimed_when_unset() -> None:
    assert "Il tuo nome è" not in _system_text(_ctx())


def test_proactive_prompt_drops_the_multi_message_rule() -> None:
    """Sul percorso proattivo non è arrivato nessun messaggio: chiedere di
    rispondere «a tutto ciò che vedi negli ultimi turni» contraddice la direttiva."""
    orch = ConversationOrchestrator(router=None)
    text = orch._build_proactive_messages(_ctx(), "ricontatta il lead", "")[0].content
    assert "COME SCRIVERE" in text
    assert "più messaggi di fila" not in text
