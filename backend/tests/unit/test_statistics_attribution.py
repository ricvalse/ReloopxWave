"""Statistiche configurabili: attribuzione, esiti, profili (unit).

Copre i pezzi che rendono rispondibili le tre domande del merchant — quanti
messaggi ho inviato, a quanti è stato risposto, quanti hanno dichiarato un
esito — e le invarianti che li tengono corretti:

  - la regola last-touch attribuisce solo la **prima** risposta a un invio (senza
    la quale il reply-rate può superare il 100%);
  - i nuovi tipi di nodo sono nella tassonomia e la loro config è validata;
  - i cancelli deterministici (`conversation_profile`, `last_touch_node`) sono
    condizioni pure, valutabili anche sul percorso sincrono;
  - `has_outcome` è async e fallisce chiusa;
  - il filtro `profile_id` sul trigger scarta un evento di un altro profilo
    *prima* di accodare il job;
  - lo schema delle metriche pretende che ogni sorgente porti il proprio
    riferimento (una bolla `outcome` senza `outcome_id` è un errore di
    validazione, non una bolla che mostra zero per sempre).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from workers.automation import engine
from workers.automation.engine import RunContext, _trigger_config_match

from ai_core.automations import (
    _ASYNC_CONDITION_TYPES,
    _ATOMIC_CONDITION_TYPES,
    evaluate_condition,
    validate_graph,
)
from config_resolver.schema import (
    STRUCTURAL_METRIC_PRESETS,
    DashboardConfig,
    MetricDefinitionSchema,
)
from db.models.automation import ACTION_TYPES, CONDITION_TYPES
from db.repositories.message import MessageRepository


def _run_ctx(**over: Any) -> RunContext:
    base: dict[str, Any] = {
        "phone": "393331112233",
        "wa_phone_number_id": "pnid",
        "within_window": True,
        "score": 50,
        "temperature": "warm",
        "name": "Mario Rossi",
        "last_message": "sì l'ho compilato",
        "lead_id": uuid4(),
        "conversation_id": uuid4(),
        "tenant_id": uuid4(),
        "merchant_id": uuid4(),
    }
    base.update(over)
    return RunContext(**base)


# ---- Tassonomia dei nodi ---------------------------------------------------


def test_new_node_types_are_in_the_taxonomy() -> None:
    for cond in ("conversation_profile", "last_touch_node", "has_outcome"):
        assert cond in CONDITION_TYPES
    for action in ("emit_outcome", "set_conversation_profile"):
        assert action in ACTION_TYPES


def test_only_io_bound_conditions_are_async() -> None:
    # `conversation_profile` e `last_touch_node` leggono valori precalcolati nel
    # RunContext: devono restare atomici, altrimenti un condition_group sul
    # percorso sincrono (schedulers) li fallirebbe chiusi.
    assert "conversation_profile" in _ATOMIC_CONDITION_TYPES
    assert "last_touch_node" in _ATOMIC_CONDITION_TYPES
    assert frozenset({"ai_check", "has_outcome"}) == _ASYNC_CONDITION_TYPES


# ---- Cancelli deterministici ------------------------------------------------


def test_conversation_profile_condition() -> None:
    profile = uuid4()
    ctx = _run_ctx(profile_id=profile).as_condition_context()
    assert evaluate_condition("conversation_profile", {"profile_id": str(profile)}, ctx)
    assert not evaluate_condition("conversation_profile", {"profile_id": str(uuid4())}, ctx)
    # Config vuota non deve mai valere "qualunque profilo".
    assert not evaluate_condition("conversation_profile", {}, ctx)


def test_last_touch_node_condition() -> None:
    automation = uuid4()
    ctx = _run_ctx(
        last_touch_node_key="chiedi_questionario",
        last_touch_automation_id=automation,
    ).as_condition_context()

    assert evaluate_condition("last_touch_node", {"node_key": "chiedi_questionario"}, ctx)
    assert not evaluate_condition("last_touch_node", {"node_key": "altro_nodo"}, ctx)
    # Con automation_id specificato, deve combaciare anche quello.
    assert evaluate_condition(
        "last_touch_node",
        {"node_key": "chiedi_questionario", "automation_id": str(automation)},
        ctx,
    )
    assert not evaluate_condition(
        "last_touch_node",
        {"node_key": "chiedi_questionario", "automation_id": str(uuid4())},
        ctx,
    )


def test_last_touch_node_is_false_without_a_previous_touch() -> None:
    ctx = _run_ctx().as_condition_context()
    assert not evaluate_condition("last_touch_node", {"node_key": "chiedi_questionario"}, ctx)


@pytest.mark.asyncio
async def test_has_outcome_fails_closed_without_session() -> None:
    passed = await engine._evaluate_has_outcome(
        {"outcome_id": str(uuid4())}, _run_ctx(), session=None, label="n1"
    )
    assert passed is False


@pytest.mark.asyncio
async def test_has_outcome_rejects_a_malformed_id() -> None:
    passed = await engine._evaluate_has_outcome(
        {"outcome_id": "non-un-uuid"}, _run_ctx(), session=object(), label="n1"
    )
    assert passed is False


# ---- Filtro sul trigger -----------------------------------------------------


def test_trigger_profile_filter_discards_other_profiles() -> None:
    """Il filtro sta nel dispatcher, prima dell'accodamento.

    È la differenza fra non far partire il job e farlo partire, costruire le
    dipendenze AI e poi scartarlo.
    """
    wanted, other = uuid4(), uuid4()
    event = SimpleNamespace(profile_id=wanted)

    assert _trigger_config_match({"profile_id": str(wanted)}, {}, event=event)
    assert not _trigger_config_match({"profile_id": str(other)}, {}, event=event)
    # Config senza profilo = nessun filtro (comportamento storico invariato).
    assert _trigger_config_match({}, {}, event=event)
    # Ripiego su properties per gli emettitori non ancora aggiornati.
    assert _trigger_config_match(
        {"profile_id": str(wanted)}, {"profile_id": str(wanted)}, event=SimpleNamespace()
    )


# ---- Attribuzione last-touch ------------------------------------------------


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalars(self) -> _FakeResult:
        return self

    def first(self) -> Any:
        return self._row


class _FakeSession:
    def __init__(self, last: Any) -> None:
        self._last = last

    async def execute(self, *_a: Any, **_kw: Any) -> _FakeResult:
        return _FakeResult(self._last)


@pytest.mark.asyncio
async def test_reply_target_is_the_last_outbound() -> None:
    outbound = SimpleNamespace(id=uuid4(), direction="out")
    repo = MessageRepository(_FakeSession(outbound))
    assert await repo.resolve_reply_target(uuid4()) is outbound


@pytest.mark.asyncio
async def test_reply_target_is_none_when_the_lead_is_already_talking() -> None:
    """Solo la PRIMA risposta a un invio viene attribuita.

    Senza questa regola un lead che scrive tre messaggi di fila conterebbe tre
    risposte per un solo invio, e il reply-rate supererebbe il 100%.
    """
    inbound = SimpleNamespace(id=uuid4(), direction="in")
    repo = MessageRepository(_FakeSession(inbound))
    assert await repo.resolve_reply_target(uuid4()) is None


@pytest.mark.asyncio
async def test_reply_target_is_none_on_an_empty_thread() -> None:
    repo = MessageRepository(_FakeSession(None))
    assert await repo.resolve_reply_target(uuid4()) is None


# ---- Schema delle metriche --------------------------------------------------


def test_metric_sources_require_their_reference() -> None:
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(id="q", label="Questionario", source="outcome")
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(id="e", label="Eventi", source="event")
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(id="m", label="Messaggi", source="messages")


def test_metric_event_type_must_be_in_the_catalog() -> None:
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(
            id="typo", label="Promemoria", source="event", event_type="reminder.sent"
        )
    # Quello vero passa.
    ok = MetricDefinitionSchema(
        id="ok", label="Promemoria", source="event", event_type="appointment_reminder.sent"
    )
    assert ok.event_type == "appointment_reminder.sent"


def test_structural_presets_are_valid_and_share_one_set() -> None:
    """Inviati e risposti devono essere lo STESSO insieme letto due volte.

    È ciò che rende il loro rapporto un tasso di risposta sensato invece di due
    misure scorrelate.
    """
    presets = {p["id"]: MetricDefinitionSchema(**p) for p in STRUCTURAL_METRIC_PRESETS}
    sent = presets["automation_messages_sent"]
    replied = presets["automation_replies_received"]

    assert sent.direction == replied.direction == "out"
    assert sent.sender_types == replied.sender_types
    assert sent.has_reply is None
    assert replied.has_reply is True


def test_default_dashboard_is_unchanged_by_the_new_sources() -> None:
    """Un merchant senza override vede esattamente le bolle di prima."""
    metrics = DashboardConfig().metrics
    assert [m.id for m in metrics] == [
        "bookings_created",
        "messages_received",
        "messages_replied",
        "pipeline_moved",
        "reminders_sent",
    ]
    assert all(m.source == "event" for m in metrics)


def test_metric_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError):
        DashboardConfig(
            metrics=[
                {"id": "dup", "label": "A", "source": "event", "event_type": "booking.created"},
                {"id": "dup", "label": "B", "source": "event", "event_type": "pipeline.moved"},
            ]
        )


# ---- Validazione del grafo --------------------------------------------------


def _graph(node_type: str, kind: str, config: dict[str, Any]) -> tuple[list, list]:
    nodes = [
        {"node_key": "t1", "kind": "trigger", "type": "message_received", "config": {}},
        {"node_key": "n1", "kind": kind, "type": node_type, "config": config},
    ]
    edges = [{"source_key": "t1", "target_key": "n1", "branch": "default"}]
    return nodes, edges


def test_emit_outcome_requires_an_outcome_id() -> None:
    nodes, edges = _graph("emit_outcome", "action", {})
    assert not validate_graph(nodes, edges).ok

    nodes, edges = _graph("emit_outcome", "action", {"outcome_id": str(uuid4())})
    assert validate_graph(nodes, edges).ok


def test_emit_outcome_rejects_an_out_of_range_confidence() -> None:
    nodes, edges = _graph("emit_outcome", "action", {"outcome_id": str(uuid4()), "confidence": 1.5})
    assert not validate_graph(nodes, edges).ok


def test_set_conversation_profile_requires_a_profile_id() -> None:
    nodes, edges = _graph("set_conversation_profile", "action", {})
    assert not validate_graph(nodes, edges).ok

    nodes, edges = _graph("set_conversation_profile", "action", {"profile_id": str(uuid4())})
    assert validate_graph(nodes, edges).ok


def test_gate_conditions_require_their_reference() -> None:
    for node_type, good in (
        ("has_outcome", {"outcome_id": str(uuid4())}),
        ("conversation_profile", {"profile_id": str(uuid4())}),
        ("last_touch_node", {"node_key": "chiedi_questionario"}),
    ):
        nodes, edges = _graph(node_type, "condition", {})
        assert not validate_graph(nodes, edges).ok, node_type

        nodes, edges = _graph(node_type, "condition", good)
        assert validate_graph(nodes, edges).ok, node_type
