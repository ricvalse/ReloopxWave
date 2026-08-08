"""UC-03 — no-answer trigger emitter unit tests (ADR 0015 / 0025 / 0029).

The scheduler no longer sends: it emits a `lead.no_answer` analytics event once
per silence episode, which the automation engine dispatches to the merchant's
flow. These tests stub the repos and check:
  - idle past the trigger's `delay_minutes` + an enabled automation → emits + anchors;
  - no enabled `no_answer` automation → nothing emitted (and no anchor burned);
  - already fired for this silence episode → suppressed;
  - a fresh inbound (last_inbound_at past the old anchor) re-arms the trigger;
  - still within the configured delay → not yet emitted;
  - ADR 0025: il lead che non ha MAI risposto è un silenzio valido;
  - ADR 0029: ogni automazione ha la sua soglia, la sua ancora e il suo filtro
    di provenienza ("non ha risposto **a questo** template").
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from workers.scheduler import no_answer

from db import ReminderCandidate


def _fake_flow(
    delay_minutes: int = 120,
    *,
    flow_id: uuid.UUID | None = None,
    source_template_id: str | None = None,
) -> Any:
    config: dict[str, Any] = {"delay_minutes": delay_minutes}
    if source_template_id is not None:
        config["source_template_id"] = source_template_id
    trigger = SimpleNamespace(kind="trigger", type="no_answer", config=config)
    return SimpleNamespace(id=flow_id or uuid.uuid4(), nodes=[trigger], edges=[])


def _candidate(**over: Any) -> ReminderCandidate:
    now = datetime.now(tz=UTC)
    base: dict[str, Any] = dict(
        conversation_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        wa_phone_number_id="PNID-1",
        wa_contact_phone="39333000000",
        last_message_at=now - timedelta(hours=3),
        started_at=now - timedelta(hours=4),
        last_inbound_at=now - timedelta(hours=3),
        no_answer_fired_for={},
    )
    base.update(over)
    return ReminderCandidate(**base)


def _patch(monkeypatch: pytest.MonkeyPatch, *, flows: list, marks: list, events: list) -> None:
    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeAutoRepo:
        def __init__(self, session): ...
        async def list_enabled_by_trigger(self, *, merchant_id, trigger_type):
            assert trigger_type == "no_answer"
            return flows

    class FakeConvRepo:
        def __init__(self, session): ...
        async def mark_no_answer_fired(self, conversation_id, automation_id, anchor):
            marks.append((conversation_id, automation_id, anchor))

    class FakeAnalytics:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    monkeypatch.setattr(no_answer, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(no_answer, "AutomationRepository", FakeAutoRepo)
    monkeypatch.setattr(no_answer, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(no_answer, "AnalyticsRepository", FakeAnalytics)


async def test_emits_when_idle_past_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    flow = _fake_flow(120)
    _patch(monkeypatch, flows=[flow], marks=marks, events=events)
    cand = _candidate()  # idle 3h > 120 min

    n = await no_answer._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert n == 1
    assert marks == [(cand.conversation_id, flow.id, cand.last_inbound_at)]
    assert events and events[0]["event_type"] == "lead.no_answer"
    assert events[0]["subject_type"] == "conversation"
    assert events[0]["subject_id"] == cand.conversation_id
    assert events[0]["properties"]["episode_anchor"] == cand.last_inbound_at.isoformat()
    assert events[0]["properties"]["target_automation_id"] == str(flow.id)


async def test_skips_when_no_enabled_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[], marks=marks, events=events)

    n = await no_answer._maybe_emit(_candidate(), now=datetime.now(tz=UTC))

    assert n == 0
    assert marks == [] and events == []


async def test_skips_when_already_fired_for_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    flow = _fake_flow()
    _patch(monkeypatch, flows=[flow], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    anchor = now - timedelta(hours=3)
    cand = _candidate(last_inbound_at=anchor, no_answer_fired_for={str(flow.id): anchor})

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 0
    assert events == []


async def test_legacy_scalar_anchor_still_suppresses(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'ancora vecchia (una sola per conversazione) vale per ogni automazione."""
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow()], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    anchor = now - timedelta(hours=3)
    cand = _candidate(
        last_inbound_at=anchor,
        no_answer_fired_for={no_answer.ANCHOR_ANY: anchor},
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 0


async def test_re_arms_after_new_inbound(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    flow = _fake_flow()
    _patch(monkeypatch, flows=[flow], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    # A new inbound arrived AFTER the previous fired anchor → the trigger re-arms.
    cand = _candidate(
        last_message_at=now - timedelta(hours=3),
        last_inbound_at=now - timedelta(hours=2),
        no_answer_fired_for={str(flow.id): now - timedelta(hours=5)},
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 1
    assert events and events[0]["event_type"] == "lead.no_answer"


async def test_skips_when_not_idle_past_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(600)], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    # Idle 180 min < the trigger's 600-min delay → not yet due.
    cand = _candidate(
        last_message_at=now - timedelta(minutes=180),
        last_inbound_at=now - timedelta(minutes=180),
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 0
    assert events == []


# --- ADR 0025: il lead che non ha MAI risposto ------------------------------
#
# Il caso che il gate `last_inbound_at IS NOT NULL` escludeva: primo contatto in
# uscita, silenzio totale. È il silenzio più comune su un merchant che fa
# outreach, e fino al fix non emetteva nulla.


async def test_emits_when_lead_never_replied(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    flow = _fake_flow(240)
    _patch(monkeypatch, flows=[flow], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    started = now - timedelta(hours=6)
    cand = _candidate(
        started_at=started,
        last_inbound_at=None,  # gli abbiamo scritto noi, lui non ha mai risposto
        last_message_at=now - timedelta(hours=5),  # idle 300 min > 240
        no_answer_fired_for={},
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 1
    # L'ancora è `started_at`, non `last_message_at`: è ciò che rende l'emissione
    # one-shot invece che ciclica (vedi il test qui sotto).
    assert marks == [(cand.conversation_id, flow.id, started)]
    assert events[0]["properties"]["episode_anchor"] == started.isoformat()
    assert events[0]["properties"]["never_replied"] is True


async def test_never_replied_does_not_re_fire_after_our_own_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il sollecito fa avanzare `last_message_at`: l'ancora NON deve seguirlo.

    È il modo in cui questo fix poteva diventare un loop — mandare il sollecito,
    riarmare il trigger sul proprio stesso invio, e ricominciare ogni
    `delay_minutes` per sempre, senza che il lead abbia fatto niente.
    """
    marks: list = []
    events: list = []
    flow = _fake_flow(240)
    _patch(monkeypatch, flows=[flow], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    started = now - timedelta(hours=20)
    cand = _candidate(
        started_at=started,
        last_inbound_at=None,
        # Il sollecito è partito 5 ore fa e ha bumpato `last_message_at`: la
        # conversazione è di nuovo "idle da 300 min > 240".
        last_message_at=now - timedelta(hours=5),
        no_answer_fired_for={str(flow.id): started},  # ancora già bruciata
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 0
    assert events == [] and marks == []


async def test_never_replied_re_arms_once_the_lead_finally_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Se il lead risponde e poi torna in silenzio, è un episodio nuovo."""
    marks: list = []
    events: list = []
    flow = _fake_flow(240)
    _patch(monkeypatch, flows=[flow], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    started = now - timedelta(days=2)
    replied = now - timedelta(hours=6)
    cand = _candidate(
        started_at=started,
        last_inbound_at=replied,  # ha risposto, dopo l'ancora bruciata
        last_message_at=now - timedelta(hours=5),
        no_answer_fired_for={str(flow.id): started},
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 1
    assert marks == [(cand.conversation_id, flow.id, replied)]
    assert events[0]["properties"]["never_replied"] is False


async def test_never_replied_still_respects_the_configured_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(240)], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    cand = _candidate(
        started_at=now - timedelta(hours=4),
        last_inbound_at=None,
        last_message_at=now - timedelta(minutes=180),  # idle 180 < 240
        no_answer_fired_for={},
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 0
    assert events == []


# --- ADR 0029: provenienza + una soglia e un'ancora per automazione ---------


async def test_source_filter_matches_only_its_own_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Nessuna risposta al template X, e solo a quello"."""
    template_x = str(uuid.uuid4())
    marks: list = []
    events: list = []
    _patch(
        monkeypatch,
        flows=[_fake_flow(60, source_template_id=template_x)],
        marks=marks,
        events=events,
    )
    now = datetime.now(tz=UTC)
    cand = _candidate(
        last_message_at=now - timedelta(hours=3),
        last_outbound_template_id=uuid.UUID(template_x),
        last_outbound_template_name="reloop_sollecito_questionario",
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 1
    assert events[0]["properties"]["source_template_id"] == template_x
    assert events[0]["properties"]["source_template_name"] == "reloop_sollecito_questionario"


async def test_source_filter_skips_a_different_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marks: list = []
    events: list = []
    _patch(
        monkeypatch,
        flows=[_fake_flow(60, source_template_id=str(uuid.uuid4()))],
        marks=marks,
        events=events,
    )
    now = datetime.now(tz=UTC)
    cand = _candidate(
        last_message_at=now - timedelta(hours=3),
        last_outbound_template_id=uuid.uuid4(),  # un altro template
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 0
    # L'ancora NON va bruciata: l'automazione non era in gioco per questo
    # episodio, e deve poter partire se in futuro lo diventa.
    assert marks == []


async def test_source_filter_skips_when_last_outbound_was_not_a_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una risposta dell'AI o una frase di un operatore non è "quel tocco"."""
    marks: list = []
    events: list = []
    _patch(
        monkeypatch,
        flows=[_fake_flow(60, source_template_id=str(uuid.uuid4()))],
        marks=marks,
        events=events,
    )
    now = datetime.now(tz=UTC)
    cand = _candidate(
        last_message_at=now - timedelta(hours=3),
        last_outbound_template_id=None,
        last_outbound_template_name=None,
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 0


async def test_no_source_filter_keeps_the_historic_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Campo vuoto = nessun filtro: le automazioni esistenti non cambiano."""
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(60)], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    cand = _candidate(
        last_message_at=now - timedelta(hours=3),
        last_outbound_template_id=None,
    )

    n = await no_answer._maybe_emit(cand, now=now)

    assert n == 1


async def test_two_automations_each_fire_at_their_own_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il bug che ADR 0029 chiude.

    Con soglia collassata al `min()` e un'ancora sola, a 90 minuti partivano
    entrambe (quella da 240 in anticipo di due ore e mezza) e l'ancora bruciata
    impediva alla seconda di partire quando sarebbe stato il suo momento.
    """
    breve = _fake_flow(60)
    lunga = _fake_flow(240)
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[breve, lunga], marks=marks, events=events)
    # L'ultimo messaggio (e l'ultimo inbound) sono lo stesso istante T: è
    # l'episodio di silenzio che le due automazioni guardano con occhi diversi.
    t_zero = datetime.now(tz=UTC) - timedelta(minutes=300)

    # A T+90 solo quella da 60 ha titolo a partire.
    cand = _candidate(last_message_at=t_zero, last_inbound_at=t_zero, no_answer_fired_for={})
    n = await no_answer._maybe_emit(cand, now=t_zero + timedelta(minutes=90))
    assert n == 1
    assert marks == [(cand.conversation_id, breve.id, t_zero)]

    # A T+300 parte anche quella da 240, sullo stesso episodio, perché la sua
    # ancora è distinta da quella già bruciata dalla prima.
    events.clear()
    marks.clear()
    maturo = _candidate(
        conversation_id=cand.conversation_id,
        last_message_at=t_zero,
        last_inbound_at=t_zero,
        no_answer_fired_for={str(breve.id): t_zero},
    )
    n = await no_answer._maybe_emit(maturo, now=t_zero + timedelta(minutes=300))
    assert n == 1
    assert marks == [(cand.conversation_id, lunga.id, t_zero)]
    assert events[0]["properties"]["target_automation_id"] == str(lunga.id)
