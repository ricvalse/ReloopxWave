"""Unit tests for the 24h-window outbound dispatcher (compliance core).

Content comes EXCLUSIVELY from the lavagnetta send node (`step.free_text`) or a
bound approved template — there is NO hardcoded fallback copy (ADR 0014). A blank
send node (no free_text, no approved template) → SKIP ("no_content").
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from workers import outbound
from workers.outbound import (
    MODE_SKIP,
    MODE_TEMPLATE,
    MODE_TEXT,
    OutboundDecision,
    decide_outbound,
    is_within_24h,
    render_free_text,
    send_and_persist_decision,
)

from db import ResolvedFlowStep

NOW = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)


def _step(**over: object) -> ResolvedFlowStep:
    """An enabled send step. Defaults: no free_text, but a bound APPROVED template
    (so the template path is exercised); override `free_text` / `template_*` per test."""
    base: dict[str, object] = {
        "flow_enabled": True,
        "step_enabled": True,
        "window_policy": "auto",
        "free_text": None,
        "variable_mapping": {"1": "contact.phone"},
        "template_name": "reloop_reactivation_x",
        "template_language": "it",
        "template_variables": ["1"],
        "template_approved": True,
    }
    base.update(over)
    return ResolvedFlowStep(**base)  # type: ignore[arg-type]


def test_is_within_24h() -> None:
    assert is_within_24h(NOW - timedelta(hours=1), NOW) is True
    assert is_within_24h(NOW - timedelta(hours=25), NOW) is False
    assert is_within_24h(None, NOW) is False


def test_no_flow_inside_window_skips() -> None:
    # ADR 0014: no configured/enabled flow → SKIP even inside the window.
    d = decide_outbound(within_window=True, step=None)
    assert d.mode == MODE_SKIP
    assert d.reason == "no_flow"


def test_no_flow_outside_window_skips() -> None:
    d = decide_outbound(within_window=False, step=None)
    assert d.mode == MODE_SKIP
    assert d.reason == "no_flow"


def test_disabled_flow_skips() -> None:
    d = decide_outbound(within_window=True, step=_step(flow_enabled=False))
    assert d.mode == MODE_SKIP
    assert d.reason == "flow_disabled"


def test_blank_send_node_inside_window_skips_no_content() -> None:
    # ADR 0014: an enabled flow whose send node has no free_text AND no approved
    # template sends NOTHING — no hardcoded copy is invented.
    d = decide_outbound(
        within_window=True,
        step=_step(free_text=None, template_name=None, template_approved=False),
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "no_content"


def test_step_free_text_sends_inside_window() -> None:
    d = decide_outbound(within_window=True, step=_step(free_text="copia dalla lavagnetta"))
    assert d.mode == MODE_TEXT
    assert d.text == "copia dalla lavagnetta"


def test_auto_inside_window_no_text_uses_approved_template() -> None:
    # No free_text but a bound approved template → send the (canvas-configured)
    # template even inside the window, rather than inventing copy.
    d = decide_outbound(
        within_window=True, step=_step(free_text=None), context={"contact.phone": "39333"}
    )
    assert d.mode == MODE_TEMPLATE
    assert d.template_name == "reloop_reactivation_x"


def test_auto_outside_window_with_approved_template_sends_template() -> None:
    d = decide_outbound(within_window=False, step=_step(), context={"contact.phone": "39333"})
    assert d.mode == MODE_TEMPLATE
    assert d.template_name == "reloop_reactivation_x"
    assert d.components == [{"type": "body", "parameters": [{"type": "text", "text": "39333"}]}]


def test_auto_outside_window_no_template_skips() -> None:
    d = decide_outbound(
        within_window=False, step=_step(template_name=None, template_approved=False)
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "no_template_outside_window"


def test_require_template_without_approval_skips() -> None:
    d = decide_outbound(
        within_window=True,
        step=_step(window_policy="require_template", template_approved=False),
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "no_approved_template"


def test_freeform_only_outside_window_skips() -> None:
    d = decide_outbound(within_window=False, step=_step(window_policy="freeform_only"))
    assert d.mode == MODE_SKIP
    assert d.reason == "outside_window_freeform_only"


def test_freeform_only_inside_window_blank_skips_no_content() -> None:
    d = decide_outbound(
        within_window=True, step=_step(window_policy="freeform_only", free_text=None)
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "no_content"


def test_template_with_unmapped_variables_skips_instead_of_broken_send() -> None:
    # Template declares {{1}} but the step has no mapping → params would resolve
    # to "" and Meta would reject the send; we skip instead.
    d = decide_outbound(
        within_window=False,
        step=_step(variable_mapping={}),
        context={},
    )
    assert d.mode == MODE_SKIP
    assert d.reason == "incomplete_template_mapping"


def test_render_free_text_placeholders() -> None:
    ctx = {"contact.name": "Mario Rossi", "appointment.datetime": "12/04 alle 10:00"}
    assert render_free_text("Ciao {name}", ctx) == "Ciao Mario Rossi"
    assert render_free_text("Ciao {first_name}", ctx) == "Ciao Mario"
    assert (
        render_free_text("Promemoria: {{appointment.datetime}}", ctx)
        == "Promemoria: 12/04 alle 10:00"
    )
    # Unknown {{key}} → "" (never left as raw braces); stray single braces untouched.
    assert render_free_text("x {{unknown.key}} {y}", ctx) == "x  {y}"
    assert render_free_text("", ctx) == ""


def test_decide_outbound_renders_free_text_placeholders() -> None:
    # Free text (MODE_TEXT) resolves placeholders from the template context —
    # {name} / {{appointment.datetime}} never go out raw.
    d = decide_outbound(
        within_window=True,
        step=_step(free_text="Ciao {name}, promemoria {{appointment.datetime}}"),
        context={"contact.name": "Anna", "appointment.datetime": "10:00"},
    )
    assert d.mode == MODE_TEXT
    assert d.text == "Ciao Anna, promemoria 10:00"


def test_template_decision_carries_rendered_body_as_inbox_copy() -> None:
    # A template send now carries the rendered body on `.text` so the inbox row
    # shows real content instead of an empty bubble — the params substitute into
    # the template body ({{1}}), not just the wire components.
    d = decide_outbound(
        within_window=False,
        step=_step(
            template_body="Promemoria per {{1}}",
            variable_mapping={"1": "contact.phone"},
        ),
        context={"contact.phone": "Anna"},
    )
    assert d.mode == MODE_TEMPLATE
    assert d.text == "Promemoria per Anna"


def test_template_decision_without_body_has_no_text() -> None:
    # No body configured and no free text → `.text` is None; the persistence
    # layer supplies a "Template «name»" placeholder so the send stays visible.
    d = decide_outbound(
        within_window=False,
        step=_step(template_body=None, free_text=None),
        context={"contact.phone": "39333"},
    )
    assert d.mode == MODE_TEMPLATE
    assert d.text is None


class _FakeSender:
    async def send_text(self, *, to_phone, text):
        return {"messages": [{"id": "wamid.text"}]}

    async def send_template(self, *, to_phone, template_name, language, components):
        return {"messages": [{"id": "wamid.tmpl"}]}


def _patch_message_repo(monkeypatch: pytest.MonkeyPatch, captured: list) -> None:
    class FakeMessageRepo:
        def __init__(self, session): ...
        async def persist_outbound_message(self, **kw):
            captured.append(kw)
            return object()

    monkeypatch.setattr(outbound, "MessageRepository", FakeMessageRepo)


async def test_persist_template_send_uses_rendered_body_as_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list = []
    _patch_message_repo(monkeypatch, captured)
    decision = OutboundDecision(
        mode=MODE_TEMPLATE,
        text="Promemoria per Anna",
        template_name="promo",
        template_language="it",
        components=[],
    )
    await send_and_persist_decision(
        _FakeSender(),
        to_phone="39333",
        decision=decision,
        session=object(),
        conversation_id=uuid4(),
        merchant_id=uuid4(),
        sender_type="automation",
    )
    assert len(captured) == 1
    assert captured[0]["content"] == "Promemoria per Anna"
    assert captured[0]["meta"]["kind"] == "template"
    assert captured[0]["meta"]["sender_type"] == "automation"


async def test_persist_template_send_falls_back_to_placeholder_when_no_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A template with neither rendered body nor free text must NOT persist an
    # empty bubble — the send would be invisible in the inbox.
    captured: list = []
    _patch_message_repo(monkeypatch, captured)
    decision = OutboundDecision(
        mode=MODE_TEMPLATE, text=None, template_name="promo", template_language="it", components=[]
    )
    await send_and_persist_decision(
        _FakeSender(),
        to_phone="39333",
        decision=decision,
        session=object(),
        conversation_id=uuid4(),
        merchant_id=uuid4(),
        sender_type="automation",
    )
    assert captured[0]["content"] == "Template «promo»"


# ---- le variabili numerate nel testo libero --------------------------------
#
# Sul nodo `send` convivono un testo libero e un template. Il template si scrive
# con `{{1}}`; il testo libero, prima, no — "1" veniva cercato fra le chiavi di
# contesto, non c'era, e diventava vuoto. Il cliente riceveva «Ciao , il team
# HR…». Ora la sintassi è una sola e la mappatura si imposta una volta sola.

_MAPPATURA = {"1": "lead.first_name", "2": "appointment.datetime"}
_CTX = {
    "lead.first_name": "Sara",
    "lead.name": "Sara Bianchi",
    "contact.name": "Sara Bianchi",
    "appointment.datetime": "giovedì alle 15",
}


def _solo_testo(**over: object) -> ResolvedFlowStep:
    """Un nodo col solo testo libero: isola il percorso che stiamo verificando."""
    base: dict[str, object] = {
        "free_text": "Ciao {{1}}, tutto bene?",
        "variable_mapping": _MAPPATURA,
        "template_name": None,
        "template_language": None,
        "template_variables": [],
        "template_approved": False,
    }
    base.update(over)
    return _step(**base)


def test_il_testo_libero_risolve_le_variabili_numerate_del_template() -> None:
    reso = render_free_text("Ciao {{1}}, ci vediamo {{2}}?", _CTX, _MAPPATURA)
    assert reso == "Ciao Sara, ci vediamo giovedì alle 15?"


def test_il_caso_del_cliente_non_perde_piu_il_nome() -> None:
    """La riga esatta che il merchant aveva scritto sul nodo."""
    scritto = (
        "Ciao {{1}}, il team HR sta per prendere in esame la tua candidatura, "
        "mi confermi di aver compilato il questionario? Grazie"
    )
    reso = render_free_text(scritto, _CTX, {"1": "lead.first_name"})
    assert reso.startswith("Ciao Sara,")
    assert "{{1}}" not in reso


def test_le_forme_storiche_continuano_a_funzionare() -> None:
    """Chiavi puntate e `{name}` non devono essere toccate dall'aggiunta."""
    assert render_free_text("Promemoria: {{appointment.datetime}}", _CTX, _MAPPATURA) == (
        "Promemoria: giovedì alle 15"
    )
    assert render_free_text("Ciao {first_name}", _CTX, _MAPPATURA) == "Ciao Sara"
    assert render_free_text("Ciao {name}", _CTX, _MAPPATURA) == "Ciao Sara Bianchi"
    # Senza mappatura si comporta come prima: nessuna regressione per chi non la usa.
    assert render_free_text("Promemoria: {{appointment.datetime}}", _CTX) == (
        "Promemoria: giovedì alle 15"
    )


def test_uno_slot_senza_mappatura_resta_vuoto_nella_resa() -> None:
    """La resa non inventa niente e non lascia parentesi grezze: sostituisce col
    vuoto, spazio incluso. È `decide_outbound` a decidere se un testo così si
    può mandare."""
    assert render_free_text("Ciao {{3}}", _CTX, _MAPPATURA) == "Ciao "


def test_dentro_la_finestra_il_testo_libero_parte_col_nome_risolto() -> None:
    d = decide_outbound(within_window=True, step=_solo_testo(), context=_CTX)
    assert d.mode == MODE_TEXT
    assert d.text == "Ciao Sara, tutto bene?"


def test_una_variabile_irrisolvibile_non_manda_un_messaggio_bucato() -> None:
    """Stessa regola del template: meglio non mandare che mandare «Ciao ,»."""
    ctx = dict(_CTX)
    ctx["lead.first_name"] = ""
    ctx["lead.name"] = ""
    ctx["contact.name"] = ""

    d = decide_outbound(within_window=True, step=_solo_testo(), context=ctx)

    assert d.mode == MODE_SKIP
    assert d.reason == "incomplete_variable_mapping"


def test_col_buco_nel_testo_libero_ma_un_template_pronto_parte_il_template() -> None:
    """Meglio il template che il salto — ma solo se il template regge da solo.

    La mappatura è una sola per nodo, quindi uno slot irrisolvibile lo è per
    entrambi: l'unico caso in cui il template se la cava è quando non dichiara
    variabili proprie. Ed è il caso che conta, perché è il ripiego naturale di un
    testo libero scritto male.
    """
    passo = _solo_testo(
        free_text="Ciao {{9}}",  # slot senza mappatura: buco garantito
        template_name="promemoria",
        template_language="it",
        template_approved=True,
        template_variables=[],
        template_body="Un promemoria per te",
    )

    d = decide_outbound(within_window=True, step=passo, context=_CTX)

    assert d.mode == MODE_TEMPLATE


def test_senza_mappatura_la_guardia_non_scatta_e_la_risposta_ai_parte() -> None:
    """`ai_reply` passa il testo del modello con `variable_mapping={}`.

    Se l'AI scrivesse per caso un `{{1}}`, applicare lì la guardia vorrebbe dire
    far sparire una risposta in una conversazione dal vivo. Con la mappatura
    vuota si torna alla resa storica: lo slot diventa vuoto, ma il messaggio parte.
    """
    passo = _solo_testo(free_text="Ecco {{1}} per te", variable_mapping={})

    d = decide_outbound(within_window=True, step=passo, context=_CTX)

    assert d.mode == MODE_TEXT
    assert d.text == "Ecco  per te"


def test_il_ripiego_sul_template_dichiara_il_perche() -> None:
    """Il merchant aveva scritto un testo e ne riceve un altro: va detto."""
    passo = _solo_testo(
        free_text="Ciao {{9}}",
        template_name="promemoria",
        template_language="it",
        template_approved=True,
        template_variables=[],
        template_body="Un promemoria per te",
    )

    d = decide_outbound(within_window=True, step=passo, context=_CTX)

    assert d.mode == MODE_TEMPLATE
    assert d.reason == "free_text_incompleto_uso_template"


def test_fuori_finestra_il_testo_bucato_non_cambia_nulla() -> None:
    """Fuori dalla finestra il testo libero non parte comunque: serve il template."""
    ctx = dict(_CTX)
    ctx["lead.first_name"] = ""

    d = decide_outbound(within_window=False, step=_solo_testo(), context=ctx)

    assert d.mode == MODE_SKIP
    assert d.reason == "no_template_outside_window"
