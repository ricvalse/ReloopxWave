"""UC-02 — appointment reminder scheduler unit tests.

Stubs the DB + WhatsApp send. Checks that:
  - A booked appointment whose lead is inside the 24h window gets a free-text
    reminder, is marked reminded, and emits the analytics event.
  - Outside the window with no approved template → skipped, not marked (so it
    retries once a template lands).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from workers import outbound
from workers.scheduler import appointment_reminder as mod

from db import AppointmentReminderCandidate, ResolvedFlowStep, ResolvedWhatsAppIntegration


def _enabled_step() -> ResolvedFlowStep:
    """An enabled booking flow whose send node carries the merchant's configured
    copy from the lavagnetta (ADR 0014: content comes only from the canvas)."""
    return ResolvedFlowStep(
        flow_enabled=True,
        step_enabled=True,
        window_policy="auto",
        free_text="Promemoria appuntamento {{appointment.datetime}}",
        variable_mapping={},
        template_name=None,
        template_language=None,
    )


NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _candidate(*, last_inbound_at: datetime | None) -> AppointmentReminderCandidate:
    return AppointmentReminderCandidate(
        appointment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        phone="39333000000",
        wa_phone_number_id="PNID-1",
        last_inbound_at=last_inbound_at,
        start_at=NOW + timedelta(hours=12),
        tz_name="Europe/Rome",
    )


class _FakeConv:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        # Timbro del profilo sull'invio proattivo (migrazione 0047).
        self.profile_id = None


def _patch(
    monkeypatch,
    *,
    marked: list,
    events: list,
    integration_present: bool = True,
    persisted: list | None = None,
    stamped: list | None = None,
) -> None:
    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeApptRepo:
        def __init__(self, session): ...
        async def mark_reminded(self, appointment_id, *, at):
            marked.append(appointment_id)

    class FakeConvRepo:
        def __init__(self, session): ...
        async def get_active(self, *, merchant_id, wa_contact_phone):
            return None

        async def create(self, **kw):
            return _FakeConv()

        async def touch_last_automation(self, conversation_id):
            if stamped is not None:
                stamped.append(conversation_id)

    class FakeMessageRepo:
        def __init__(self, session): ...
        async def persist_outbound_message(self, **kw):
            if persisted is not None:
                persisted.append(kw)
            return object()

    async def fake_resolve_step(session, *, merchant_id, attempt_index, context):
        return _enabled_step()  # enabled booking_created flow, copy from the canvas

    class FakeIntegrationRepo:
        def __init__(self, session, *, kek_base64): ...
        async def resolve_whatsapp(self, phone_number_id):
            if not integration_present:
                return None
            return ResolvedWhatsAppIntegration(
                merchant_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                phone_number_id=phone_number_id,
                api_key="k",
                waba_base_url=None,
                meta={},
            )

    class FakeAnalyticsRepo:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    class FakeWAClient:
        async def send_text(self, *, to_phone, text):
            return {"messages": [{"id": "wamid.ok"}]}

        async def send_template(self, *, to_phone, template_name, language, components):
            return {"messages": [{"id": "wamid.tmpl"}]}

        async def close(self): ...

    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(mod, "AppointmentRepository", FakeApptRepo)
    monkeypatch.setattr(mod, "_resolve_booking_step", fake_resolve_step)
    monkeypatch.setattr(mod, "IntegrationRepository", FakeIntegrationRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalyticsRepo)
    monkeypatch.setattr(mod, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(outbound, "MessageRepository", FakeMessageRepo)
    # Il timbro `last_automation_at` parte da dentro `send_and_persist_decision`,
    # quindi va sostituito il riferimento che vede *quel* modulo.
    monkeypatch.setattr(outbound, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(mod, "build_whatsapp_sender", lambda **kw: FakeWAClient())


async def test_sends_reminder_inside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list = []
    events: list = []
    persisted: list = []
    _patch(monkeypatch, marked=marked, events=events, persisted=persisted)

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=2))  # inside 24h
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is True
    assert marked == [cand.appointment_id]
    assert events and events[0]["event_type"] == "appointment_reminder.sent"
    # #29: the reminder is persisted as an outbound Message with its wa id.
    assert len(persisted) == 1
    assert persisted[0]["wa_message_id"] == "wamid.ok"


async def test_il_promemoria_timbra_last_automation_sulla_conversazione(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il promemoria appuntamento apre la porta alla risposta automatica.

    ADR 0031: in modalità `solo_automazioni` il bot risponde unicamente dove
    `conversations.last_automation_at` è valorizzato. Il promemoria è il caso
    limite che rende il timbro non ovvio — non ha un'automazione dietro, è uno
    scheduler, e infatti non passa `automation_id`. Se ci fossimo appoggiati a
    quello, chi risponde "sì, confermo" a un promemoria non riceverebbe
    risposta: il caso d'uso principale di un bot di prenotazioni.

    Il timbro sta invece in `send_and_persist_decision`, che è il punto da cui
    passa *ogni* invio proattivo, quindi il promemoria è coperto senza
    eccezioni. Questo test è ciò che impedisce di spostarlo altrove.
    """
    marked: list = []
    events: list = []
    persisted: list = []
    stamped: list = []
    _patch(monkeypatch, marked=marked, events=events, persisted=persisted, stamped=stamped)

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=2))  # dentro le 24h
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is True
    # Timbrata una volta sola, e proprio la conversazione su cui è stato
    # scritto il messaggio.
    assert stamped == [persisted[0]["conversation_id"]]


async def test_skips_outside_window_without_template(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list = []
    events: list = []
    _patch(monkeypatch, marked=marked, events=events)

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=30))  # outside 24h, no template
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is False
    assert marked == []  # not marked → retried when a template is approved


async def test_no_flow_skips_and_not_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    # ADR 0014: no enabled booking flow on the canvas → no reminder is sent and the
    # appointment is NOT marked (so it still fires if the merchant enables the flow).
    marked: list = []
    events: list = []
    _patch(monkeypatch, marked=marked, events=events)

    async def no_flow(session, *, merchant_id, attempt_index, context):
        return None

    monkeypatch.setattr(mod, "_resolve_booking_step", no_flow)

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=2))  # inside window
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is False
    assert marked == []
    assert events == []


async def test_multi_reminder_picks_send_matching_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0011: with an enabled booking flow, the reminder firing at T-12h resolves
    the `send` node whose «attendi fino a 12 ore prima» matches (not always attempt 0)."""
    from dataclasses import replace

    from ai_core.automations import PlannedSend, SendPlan

    marked: list = []
    events: list = []
    _patch(monkeypatch, marked=marked, events=events)

    captured: dict = {}

    async def fake_step(session, *, merchant_id, attempt_index, context):
        captured["attempt_index"] = attempt_index
        return _enabled_step()

    async def fake_plan(session, *, merchant_id, context):
        return SendPlan(
            sends=[
                PlannedSend(0, 0, 24, {}),
                PlannedSend(1, 0, 12, {}),
                PlannedSend(2, 0, 2, {}),
            ]
        )

    monkeypatch.setattr(mod, "_resolve_booking_step", fake_step)
    monkeypatch.setattr(mod, "_resolve_booking_plan", fake_plan)

    # start_at = NOW+12h, reminder_due_at = NOW → offset firing now is 12h → index 1.
    cand = replace(
        _candidate(last_inbound_at=NOW - timedelta(hours=2)),
        reminder_due_at=NOW,
    )
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is True
    assert captured["attempt_index"] == 1


# --- ADR 0030: gli orari valgono anche per il promemoria --------------------


def _hours(*, is_open: bool, next_opening: datetime | None) -> object:
    """`resolve_automation_hours` ritorna None quando il vincolo e' spento."""
    from types import SimpleNamespace

    return SimpleNamespace(
        is_open=lambda _now=None: is_open,
        next_opening=lambda _now=None: next_opening,
    )


def _patch_hours(monkeypatch: pytest.MonkeyPatch, hours: object) -> None:
    async def _resolve(_session, _merchant_id):
        return hours

    monkeypatch.setattr(mod, "resolve_automation_hours", _resolve)


async def test_promemoria_rimandato_fuori_orario_non_consuma_la_voce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fuori orario non si invia e NON si marca: il tick dopo riprova.

    Qui la coda è già implicita nel ritentativo ogni 30 minuti — non serve una
    riga in `automation_hours_queue`. Ma non marcare è ciò che la rende vera:
    marcare consumerebbe il promemoria senza averlo mandato.
    """
    marked: list = []
    events: list = []
    _patch(monkeypatch, marked=marked, events=events)
    # Riapre alle 09:00 di domani, cioè PRIMA dell'appuntamento (NOW+12h).
    _patch_hours(
        monkeypatch,
        _hours(is_open=False, next_opening=NOW + timedelta(hours=4)),
    )

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=2))
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is False
    assert marked == [], "non marcato → il promemoria riparte al tick successivo"
    assert events == []


async def test_promemoria_lasciato_cadere_se_si_riapre_dopo_l_appuntamento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Riapertura oltre `start_at` → si lascia cadere, e si registra.

    È l'unico invio con una scadenza propria: rimandarlo comunque consegnerebbe
    un promemoria per un appuntamento già cominciato — un danno che senza il
    gate non esiste e che il rinvio introdurrebbe. Qui la voce SI consuma,
    altrimenti il promemoria scaduto verrebbe ritentato per sempre.
    """
    marked: list = []
    events: list = []
    _patch(monkeypatch, marked=marked, events=events)
    # L'appuntamento è a NOW+12h, si riapre a NOW+20h: troppo tardi.
    _patch_hours(
        monkeypatch,
        _hours(is_open=False, next_opening=NOW + timedelta(hours=20)),
    )

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=2))
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is False
    assert marked == [cand.appointment_id], "consumato: non si ritenta all'infinito"
    assert [e["event_type"] for e in events] == ["automation.send_dropped"]
    assert events[0]["properties"]["reason"] == "reopening_after_appointment"


async def test_promemoria_ignora_gli_orari_se_il_vincolo_e_spento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vincolo spento (o `mode=always`) → il promemoria parte come sempre.

    `resolve_automation_hours` ritorna None in entrambi i casi, ed è la porta da
    cui passa chi il vincolo non lo vuole: da ADR 0030 la chiave è accesa di
    default, ma resta spegnibile per merchant.
    """
    marked: list = []
    events: list = []
    _patch(monkeypatch, marked=marked, events=events, persisted=[])
    _patch_hours(monkeypatch, None)

    cand = _candidate(last_inbound_at=NOW - timedelta(hours=2))
    sent = await mod._maybe_send(cand, now=NOW, kek="unused")

    assert sent is True
    assert marked == [cand.appointment_id]
