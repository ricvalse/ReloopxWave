"""UC-06 — opt-out detection + dormant trigger emitter (ADR 0015).

Covers:
  * `_is_opt_out` — STOP/CANCELLA detection (exact, normalised) driving the
    opt-out intercept in `handle_inbound_persist`.
  * reactivation `_maybe_emit`: emits a `lead.dormant` event once per dormancy
    episode (edge-triggered on `last_interaction_at`) when an enabled `lead_dormant`
    automation exists and the lead has crossed its threshold; sends nothing itself.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from workers.scheduler import reactivation

from ai_core.conversation_service import _is_opt_out
from db import ReactivationCandidate


def test_is_opt_out_matches_exact_keywords() -> None:
    for msg in ["STOP", "stop", " Stop ", "CANCELLA", "annulla", "unsubscribe", "Stop."]:
        assert _is_opt_out(msg) is True


def test_is_opt_out_ignores_sentences() -> None:
    for msg in ["stop un attimo", "non cancellare", "vorrei fermare l'ordine", "ok grazie"]:
        assert _is_opt_out(msg) is False


# ---- reactivation trigger emitter -----------------------------------------


def _fake_flow(days: int = 90) -> Any:
    trigger = SimpleNamespace(kind="trigger", type="lead_dormant", config={"days": days})
    return SimpleNamespace(nodes=[trigger], edges=[])


def _candidate(**over: Any) -> ReactivationCandidate:
    now = datetime.now(tz=UTC)
    base: dict[str, Any] = dict(
        lead_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        phone="39333000000",
        wa_phone_number_id="PNID-1",
        last_interaction_at=now - timedelta(days=120),
        attempts_sent=0,
        last_reactivation_at=None,
        name="Mario",
        last_inbound_at=now - timedelta(days=120),
        dormant_fired_for=None,
    )
    base.update(over)
    return ReactivationCandidate(**base)


def _patch(monkeypatch: pytest.MonkeyPatch, *, flows: list, marks: list, events: list) -> None:
    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeAutoRepo:
        def __init__(self, session): ...
        async def list_enabled_by_trigger(self, *, merchant_id, trigger_type):
            assert trigger_type == "lead_dormant"
            return flows

    class FakeLeadRepo:
        def __init__(self, session): ...
        async def mark_dormant_fired(self, lead_id, anchor):
            marks.append((lead_id, anchor))

    class FakeAnalytics:
        def __init__(self, session): ...
        async def emit(self, **kw):
            events.append(kw)

    monkeypatch.setattr(reactivation, "tenant_session", fake_tenant_session)
    monkeypatch.setattr(reactivation, "AutomationRepository", FakeAutoRepo)
    monkeypatch.setattr(reactivation, "LeadRepository", FakeLeadRepo)
    monkeypatch.setattr(reactivation, "AnalyticsRepository", FakeAnalytics)


async def test_emits_when_dormant_past_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(90)], marks=marks, events=events)
    cand = _candidate()  # dormant 120 days > 90

    ok = await reactivation._maybe_emit(cand, now=datetime.now(tz=UTC))

    assert ok is True
    assert marks == [(cand.lead_id, cand.last_interaction_at)]
    assert events and events[0]["event_type"] == "lead.dormant"
    assert events[0]["subject_type"] == "lead"
    assert events[0]["subject_id"] == cand.lead_id


async def test_skips_when_no_enabled_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[], marks=marks, events=events)

    ok = await reactivation._maybe_emit(_candidate(), now=datetime.now(tz=UTC))

    assert ok is False
    assert marks == [] and events == []


async def test_skips_when_already_fired_for_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow()], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    anchor = now - timedelta(days=120)
    cand = _candidate(last_interaction_at=anchor, dormant_fired_for=anchor)

    ok = await reactivation._maybe_emit(cand, now=now)

    assert ok is False
    assert events == []


async def test_skips_when_not_dormant_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(90)], marks=marks, events=events)
    now = datetime.now(tz=UTC)
    # Dormant only 40 days < the trigger's 90-day threshold.
    cand = _candidate(last_interaction_at=now - timedelta(days=40))

    ok = await reactivation._maybe_emit(cand, now=now)

    assert ok is False
    assert events == []


# ---- il pavimento della scansione ------------------------------------------
#
# `_maybe_emit` applica la soglia del merchant, ma non vede che i candidati che
# la scansione gli passa: una soglia sotto il pavimento non arriva mai fin lì.
# Era il buco — nessun test guardava il pavimento, e per mesi è stato una
# costante di 30 giorni che scartava in silenzio ogni "2 giorni" della UI.


async def _cutoff_for(monkeypatch: pytest.MonkeyPatch, thresholds: dict) -> Any:
    """Fa girare l'emettitore e restituisce (cutoff passato alla scansione, esito)."""
    visti: list[datetime] = []

    async def fake_thresholds() -> dict:
        return thresholds

    async def fake_scan(*, dormant_cutoff: datetime) -> list:
        visti.append(dormant_cutoff)
        return []

    monkeypatch.setattr(reactivation, "_enabled_thresholds", fake_thresholds)
    monkeypatch.setattr(reactivation, "_scan_candidates", fake_scan)
    esito = await reactivation.reactivate_dormant_leads({})
    return (visti[0] if visti else None), esito


async def test_soglia_di_due_giorni_scandisce_da_due_giorni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La regressione, in una riga: 2 giorni sul nodo = 2 giorni di scansione.

    Con il vecchio pavimento fisso il cutoff cadeva 30 giorni indietro e un lead
    fermo da 2 giorni non entrava nemmeno fra i candidati.
    """
    now = datetime.now(tz=UTC)
    cutoff, _ = await _cutoff_for(monkeypatch, {uuid.uuid4(): [2]})

    assert cutoff is not None
    assert abs((cutoff - (now - timedelta(days=2))).total_seconds()) < 60
    assert cutoff > now - timedelta(days=30), "il pavimento fisso a 30 giorni è tornato"


async def test_il_pavimento_e_il_minimo_configurato_sulla_piattaforma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un merchant a 2 giorni non deve essere oscurato da uno a 90.

    Il filtro per merchant resta in `_maybe_emit`: la scansione allarga, non
    decide.
    """
    now = datetime.now(tz=UTC)
    cutoff, _ = await _cutoff_for(monkeypatch, {uuid.uuid4(): [90], uuid.uuid4(): [2, 45]})

    assert cutoff is not None
    assert abs((cutoff - (now - timedelta(days=2))).total_seconds()) < 60


async def test_nessuna_automazione_attiva_non_scandisce_affatto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff, esito = await _cutoff_for(monkeypatch, {})

    assert cutoff is None, "senza nessuno in ascolto la scansione va saltata"
    assert esito == {"candidates": 0, "emitted": 0, "skipped": "no_enabled_automations"}


async def test_il_pavimento_assoluto_resta_un_giorno(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il cron gira una volta al giorno: sotto il giorno non c'è niente da guadagnare.

    Un valore non positivo il repository lo normalizza già al default; qui si
    verifica la guardia dello scheduler, che non deve mai chiedere un cutoff nel
    futuro se quel valore gli arrivasse comunque.
    """
    now = datetime.now(tz=UTC)
    cutoff, _ = await _cutoff_for(monkeypatch, {uuid.uuid4(): [0]})

    assert cutoff is not None
    assert abs((cutoff - (now - timedelta(days=1))).total_seconds()) < 60


def test_la_query_delle_soglie_accetta_una_chiave_di_config_variabile() -> None:
    """`days` per il dormiente, `delay_minutes` per il no-answer: la chiave è un
    parametro, e deve restare compilabile come indice JSONB."""
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from db.models.automation import AutomationNode

    for chiave in ("days", "delay_minutes"):
        sql = str(
            select(AutomationNode.config[chiave].astext).compile(
                dialect=postgresql.asyncpg.dialect()
            )
        )
        assert "->>" in sql
