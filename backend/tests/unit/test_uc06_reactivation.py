"""UC-06 — opt-out detection + dormant trigger emitter (ADR 0015).

Covers:
  * `_is_opt_out` — STOP/CANCELLA detection (exact, normalised) driving the
    opt-out intercept in `handle_inbound_persist`.
  * reactivation `_maybe_emit`: emits a `lead.dormant` event once per dormancy
    episode (edge-triggered sull'ancora **immobile** `last_inbound_at or
    first_seen_at`) when an enabled `lead_dormant` automation exists and the lead
    has crossed its threshold; sends nothing itself.
  * il pavimento della scansione e la matrice dei timeframe: qualunque soglia il
    merchant scriva deve emettere appena superata, una volta sola, e senza
    trascinare i merchant configurati diversamente.
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
        # Istante immobile: per default molto più indietro dell'ultima
        # interazione, come per un lead creato all'inizio della relazione.
        first_seen_at=now - timedelta(days=200),
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
    # L'ancora timbrata è quella dell'episodio (l'ultima parola del lead), non
    # l'ultima interazione: è la differenza che impedisce il riarmo automatico.
    assert marks == [(cand.lead_id, cand.last_inbound_at)]
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
    # `last_inbound_at` esplicito: è lui l'ancora dell'episodio quando il lead ha
    # parlato almeno una volta, non `last_interaction_at`.
    cand = _candidate(last_interaction_at=anchor, last_inbound_at=anchor, dormant_fired_for=anchor)

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


async def test_il_sollecito_che_mandiamo_noi_non_riarma_il_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un lead che non risponde MAI deve ricevere un sollecito solo, non uno ogni N giorni.

    È la trappola che ADR 0025 aveva già trovato per il no-answer: se l'ancora
    dell'episodio è un istante che il **nostro** messaggio fa avanzare, il
    sollecito si autoalimenta — parte, sposta l'ancora, il trigger si riarma, e
    dopo altri `days` riparte, all'infinito, senza che il lead abbia fatto nulla.

    Qui la simulazione è esattamente quella: emissione, poi l'automazione manda
    il messaggio (che fa avanzare `last_message_at`, quindi `last_interaction_at`),
    poi passa di nuovo la soglia. Non deve emettere una seconda volta.
    """
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(2)], marks=marks, events=events)

    t0 = datetime.now(tz=UTC) - timedelta(days=10)
    nascita = t0 - timedelta(days=1)  # il lead esiste da prima: istante immobile
    cand = _candidate(
        last_interaction_at=t0,
        first_seen_at=nascita,
        last_inbound_at=None,
        dormant_fired_for=None,
    )

    # 1) primo giro: il lead è fermo da 10 giorni, la soglia è 2 → emette.
    assert await reactivation._maybe_emit(cand, now=t0 + timedelta(days=10)) is True
    assert len(events) == 1
    ancora_scritta = marks[0][1]
    assert ancora_scritta == nascita, "l'ancora timbrata deve essere l'istante immobile"

    # 2) l'automazione manda il sollecito: `last_message_at` avanza, e con lui
    #    `last_interaction_at` del lead. Il lead non ha risposto, quindi né
    #    `last_inbound_at` né la data di creazione si muovono.
    invio = t0 + timedelta(days=10)
    dopo = _candidate(
        lead_id=cand.lead_id,
        last_interaction_at=invio,
        first_seen_at=nascita,
        last_inbound_at=None,
        dormant_fired_for=ancora_scritta,
    )

    # 3) passano altri 3 giorni di silenzio: la soglia di 2 giorni è di nuovo
    #    superata. Ma è lo stesso episodio di silenzio — il lead non ha parlato.
    riemesso = await reactivation._maybe_emit(dopo, now=invio + timedelta(days=3))

    assert riemesso is False, (
        "il sollecito si autoalimenta: l'ancora si muove col nostro stesso messaggio, "
        "quindi questo lead riceverebbe un messaggio ogni `days` giorni per sempre"
    )
    assert len(events) == 1


async def test_il_trigger_si_riarma_se_il_lead_risponde(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'altra metà della regola: un episodio nuovo deve poter emettere.

    Se il lead risponde e poi sparisce di nuovo, quello è un secondo episodio di
    dormienza e il sollecito deve ripartire — altrimenti la guardia contro il
    loop diventerebbe un silenzio definitivo.
    """
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(2)], marks=marks, events=events)

    t0 = datetime.now(tz=UTC) - timedelta(days=20)
    nascita = t0 - timedelta(days=1)
    primo = _candidate(
        last_interaction_at=t0,
        first_seen_at=nascita,
        last_inbound_at=None,
        dormant_fired_for=None,
    )
    assert await reactivation._maybe_emit(primo, now=t0 + timedelta(days=5)) is True
    ancora = marks[0][1]

    # Il lead risponde 6 giorni dopo, poi tace per altri 4: `last_inbound_at`
    # avanza oltre l'ancora bruciata, quindi è un episodio nuovo.
    risposta = t0 + timedelta(days=6)
    secondo = _candidate(
        lead_id=primo.lead_id,
        last_interaction_at=risposta,
        first_seen_at=nascita,
        last_inbound_at=risposta,
        dormant_fired_for=ancora,
    )

    assert await reactivation._maybe_emit(secondo, now=risposta + timedelta(days=4)) is True
    assert len(events) == 2


# ---- la matrice dei timeframe ----------------------------------------------
#
# La domanda a cui questi test rispondono è "qualunque valore il merchant
# scriva, il sistema si comporta bene?", quindi non basta un caso: serve
# camminare il campo di valori che la UI permette, dal giorno all'anno.


@pytest.mark.parametrize("giorni", [1, 2, 3, 7, 14, 30, 90, 180, 365])
async def test_ogni_soglia_emette_appena_superata_e_non_prima(
    monkeypatch: pytest.MonkeyPatch, giorni: int
) -> None:
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(giorni)], marks=marks, events=events)

    fermo_da = datetime.now(tz=UTC) - timedelta(days=giorni + 30)
    nascita = fermo_da - timedelta(days=1)
    cand = _candidate(
        last_interaction_at=fermo_da,
        first_seen_at=nascita,
        last_inbound_at=None,
        dormant_fired_for=None,
    )

    # Un'ora prima della soglia: silenzio.
    poco_prima = fermo_da + timedelta(days=giorni) - timedelta(hours=1)
    assert await reactivation._maybe_emit(cand, now=poco_prima) is False
    assert events == []

    # Un minuto dopo la soglia: parte.
    subito_dopo = fermo_da + timedelta(days=giorni) + timedelta(minutes=1)
    assert await reactivation._maybe_emit(cand, now=subito_dopo) is True
    assert len(events) == 1
    assert events[0]["properties"]["never_replied"] is True


@pytest.mark.parametrize("giorni", [1, 2, 7, 30, 90, 365])
async def test_ogni_soglia_emette_una_volta_sola_anche_a_distanza_di_mesi(
    monkeypatch: pytest.MonkeyPatch, giorni: int
) -> None:
    """Il caso che fa la differenza fra un sollecito e uno spam ricorrente."""
    marks: list = []
    events: list = []
    _patch(monkeypatch, flows=[_fake_flow(giorni)], marks=marks, events=events)

    fermo_da = datetime.now(tz=UTC) - timedelta(days=giorni + 5)
    nascita = fermo_da - timedelta(days=1)
    base: dict[str, Any] = {"first_seen_at": nascita, "last_inbound_at": None}

    primo = _candidate(last_interaction_at=fermo_da, dormant_fired_for=None, **base)
    assert await reactivation._maybe_emit(primo, now=fermo_da + timedelta(days=giorni)) is True
    ancora = marks[0][1]

    # Sei passate successive, con il nostro sollecito che sposta ogni volta
    # l'ultima interazione: nessuna deve emettere di nuovo.
    ultima_interazione = fermo_da + timedelta(days=giorni)
    for giro in range(6):
        ultima_interazione = ultima_interazione + timedelta(days=giorni)
        cand = _candidate(
            lead_id=primo.lead_id,
            last_interaction_at=ultima_interazione,
            dormant_fired_for=ancora,
            **base,
        )
        assert (
            await reactivation._maybe_emit(cand, now=ultima_interazione + timedelta(days=giorni))
            is False
        ), f"riemesso al giro {giro + 1} con soglia {giorni} giorni"

    assert len(events) == 1


async def test_la_soglia_di_un_merchant_non_trascina_gli_altri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il pavimento della scansione è globale, la decisione no.

    Con un merchant a 2 giorni il pavimento scende a 2 per tutti: i lead di un
    merchant configurato a 90 entrano fra i candidati grezzi, e devono essere
    scartati dalla soglia del **loro** merchant.
    """
    marks: list = []
    events: list = []
    breve, lungo = uuid.uuid4(), uuid.uuid4()
    per_merchant = {breve: [_fake_flow(2)], lungo: [_fake_flow(90)]}

    @asynccontextmanager
    async def fake_tenant_session(ctx):
        yield object()

    class FakeAutoRepo:
        def __init__(self, session): ...
        async def list_enabled_by_trigger(self, *, merchant_id, trigger_type):
            return per_merchant.get(merchant_id, [])

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

    now = datetime.now(tz=UTC)
    fermo_da_3_giorni = now - timedelta(days=3)
    comune: dict[str, Any] = {
        "last_interaction_at": fermo_da_3_giorni,
        "first_seen_at": now - timedelta(days=100),
        "last_inbound_at": None,
        "dormant_fired_for": None,
    }

    assert await reactivation._maybe_emit(_candidate(merchant_id=breve, **comune), now=now) is True
    assert await reactivation._maybe_emit(_candidate(merchant_id=lungo, **comune), now=now) is False
    assert len(events) == 1


@pytest.mark.parametrize(
    ("scritto", "atteso"),
    [
        ("2", 2),
        ("365", 365),
        ("0", 90),  # non positivo -> default: 0 giorni non vuol dire niente
        ("-5", 90),
        ("", 90),  # campo svuotato nell'editor
        (None, 90),  # chiave assente (grafo importato o modificato a mano)
        ("due", 90),  # non numerico
        ("2.9", 90),  # decimale: int('2.9') solleva, quindi default
    ],
)
def test_come_viene_letto_un_valore_storto_sul_nodo(scritto: str | None, atteso: int) -> None:
    """Documenta la lettura dei valori degeneri.

    Il ripiego a 90 è deliberato, ma **non** è visibile al merchant: se svuota il
    campo la sua automazione diventa trimestrale senza che nulla glielo dica. È
    la stessa forma del difetto appena corretto, in scala minore.
    """
    from db.repositories.automation import AutomationRepository

    letto = AutomationRepository._normalizza_soglia(scritto, default=90)
    assert letto == atteso


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
