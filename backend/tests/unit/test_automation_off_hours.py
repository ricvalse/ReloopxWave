"""Gli orari del merchant valgono anche per gli invii delle automazioni (ADR 0030).

Copre le due metà del meccanismo, entrambe con fake (niente DB, niente rete):

  * il **gate nel walk** — fuori orario un nodo customer-facing non invia, ferma
    il suo ramo e si mette in `off_hours`; un nodo interno invece gira lo stesso;
  * lo **sweep di ripresa** — riaccoda `automation_run` sui nodi sospesi solo se
    il merchant ha riaperto, con claim, scadenza e rilascio.

La regressione che questi test difendono è la più insidiosa del disegno: se il
gate stesse in `_do_action` invece che nel walk, il nodo non invierebbe ma i
**successori** girerebbero comunque — e alla riapertura girerebbero una seconda
volta. Il test `test_ramo_sospeso_non_prosegue_ai_successori` è lì per quello.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from workers.automation.engine import RunContext, _walk
from workers.scheduler import flush_automation_hours_queue as sweep_mod
from workers.scheduler.flush_automation_hours_queue import _flush_one

# --- fake condivisi ---------------------------------------------------------


class _FakeSender:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.templates: list[str] = []

    async def send_text(self, *, to_phone: str, text: str) -> dict[str, Any]:
        self.texts.append(text)
        return {"messages": [{"id": "wamid.text"}]}

    async def send_template(
        self, *, to_phone: str, template_name: str, language: str, components: list
    ) -> dict[str, Any]:
        self.templates.append(template_name)
        return {"messages": [{"id": "wamid.tpl"}]}


class _FakeTemplates:
    """Sta al posto di WhatsAppTemplateRepository.get(); default: nessun template."""

    def __init__(self, template: Any = None) -> None:
        self._template = template

    async def get(self, _id: Any) -> Any:
        return self._template


def _node(node_key: str, kind: str, ntype: str, config: dict | None = None) -> Any:
    return SimpleNamespace(node_key=node_key, kind=kind, type=ntype, config=config or {})


def _edge(source: str, target: str, branch: str = "default") -> Any:
    return SimpleNamespace(source_key=source, target_key=target, branch=branch)


def _automation(nodes: list[Any], edges: list[Any]) -> Any:
    return SimpleNamespace(nodes=nodes, edges=edges)


def _run_ctx() -> RunContext:
    return RunContext(
        phone="393331112233",
        wa_phone_number_id="pnid",
        within_window=True,
        score=50,
        temperature="warm",
        name="Mario Rossi",
        last_message="ciao",
        lead_id=uuid4(),
        conversation_id=uuid4(),
        tenant_id=uuid4(),
        merchant_id=uuid4(),
    )


# --- il gate nel walk -------------------------------------------------------


async def test_dentro_gli_orari_il_nodo_invia_normalmente() -> None:
    """Il controllo di base: con `hours_closed=False` non cambia nulla."""
    automation = _automation(
        nodes=[_node("s", "action", "send_message", {"text": "promemoria"})],
        edges=[],
    )
    sender = _FakeSender()
    outcome = await _walk(
        automation,
        _run_ctx(),
        start_keys=["s"],
        sender=sender,
        templates=_FakeTemplates(),
        hours_closed=False,
    )
    assert sender.texts == ["promemoria"]
    assert outcome.sent == 1
    assert outcome.off_hours == []


@pytest.mark.parametrize("node_type", ["send_message", "send", "send_template", "ai_reply"])
async def test_fuori_orario_ogni_nodo_customer_facing_si_accoda(node_type: str) -> None:
    """Tutti e quattro i nodi che parlano al cliente vengono sospesi.

    `send_template` incluso di proposito: il template serve a parlare *fuori*
    dalla finestra di servizio WhatsApp, che è una regola di piattaforma sul
    consenso. Questa è una regola del merchant su quando il telefono del cliente
    suona — sono due vincoli diversi e valgono entrambi.
    """
    automation = _automation(
        nodes=[_node("s", "action", node_type, {"text": "x", "template_id": str(uuid4())})],
        edges=[],
    )
    sender = _FakeSender()
    outcome = await _walk(
        automation,
        _run_ctx(),
        start_keys=["s"],
        sender=sender,
        templates=_FakeTemplates(),
        hours_closed=True,
    )
    assert sender.texts == []
    assert sender.templates == []
    assert outcome.sent == 0
    assert outcome.off_hours == ["s"]


async def test_ramo_sospeso_non_prosegue_ai_successori() -> None:
    """Il ramo si ferma sul nodo sospeso, come su un `wait`.

    Se il gate stesse in `_do_action` (che ritorna False e lascia proseguire la
    coda), il successore girerebbe adesso E di nuovo alla ripresa: due invii per
    un rinvio. Qui il successore non deve essere toccato.
    """
    automation = _automation(
        nodes=[
            _node("s1", "action", "send_message", {"text": "primo"}),
            _node("s2", "action", "send_message", {"text": "secondo"}),
        ],
        edges=[_edge("s1", "s2")],
    )
    sender = _FakeSender()
    outcome = await _walk(
        automation,
        _run_ctx(),
        start_keys=["s1"],
        sender=sender,
        templates=_FakeTemplates(),
        hours_closed=True,
    )
    assert sender.texts == []
    assert outcome.off_hours == ["s1"], "solo il nodo raggiunto, non il successore"


async def test_i_nodi_interni_girano_anche_fuori_orario() -> None:
    """Un nodo-azione interno non è gateato: il walk ci passa attraverso.

    Il gate discrimina per `node.type` dentro `_CUSTOMER_FACING_NODES`, non per
    `node.kind`, quindi le condizioni non lo attraversano nemmeno — non provano
    niente. Un `set_lead_field` invece è un'azione come un `send`, e finirebbe
    gateato se il set fosse sbagliato.

    Il successore `send_message` è l'osservabile: compare in `off_hours` **solo
    se** il walk ha davvero attraversato il nodo interno. Se quello fosse
    sospeso, il ramo si fermerebbe lì e `off_hours` direbbe `["interno"]`.
    """
    automation = _automation(
        nodes=[
            _node("interno", "action", "set_lead_field", {"field": "stage", "value": "x"}),
            _node("s", "action", "send_message", {"text": "al cliente"}),
        ],
        edges=[_edge("interno", "s")],
    )
    sender = _FakeSender()
    outcome = await _walk(
        automation,
        _run_ctx(),
        start_keys=["interno"],
        sender=sender,
        templates=_FakeTemplates(),
        hours_closed=True,
    )
    assert outcome.off_hours == ["s"], "il nodo interno è stato attraversato, non sospeso"
    assert sender.texts == []


def _approved_template() -> Any:
    return SimpleNamespace(
        name="promemoria_it",
        language="it",
        variables=[],
        status="approved",
        body="Ciao, ti aspettiamo.",
        header_type=None,
        header_image_url=None,
    )


async def test_controllo_positivo_un_template_che_partirebbe_viene_trattenuto() -> None:
    """Lo stesso nodo invia a orari aperti e si accoda a orari chiusi.

    Controprova necessaria: con i fake minimi, `send`/`send_template`/`ai_reply`
    non invierebbero comunque (niente `free_text`, template assente, `ai_deps`
    None), quindi un test che guarda solo "non ha inviato" resterebbe verde
    anche cancellando il gate. Qui il template è approvato: a orari aperti parte
    davvero, ed è quella partenza che il gate deve trattenere.
    """
    automation = _automation(
        nodes=[_node("s", "action", "send_template", {"template_id": str(uuid4())})],
        edges=[],
    )
    templates = _FakeTemplates(_approved_template())

    aperto = _FakeSender()
    outcome_aperto = await _walk(
        automation,
        _run_ctx(),
        start_keys=["s"],
        sender=aperto,
        templates=templates,
        hours_closed=False,
    )
    assert aperto.templates == ["promemoria_it"], "a orari aperti questo nodo invia"
    assert outcome_aperto.sent == 1

    chiuso = _FakeSender()
    outcome_chiuso = await _walk(
        automation,
        _run_ctx(),
        start_keys=["s"],
        sender=chiuso,
        templates=templates,
        hours_closed=True,
    )
    assert chiuso.templates == [], "lo stesso identico nodo, a orari chiusi, tace"
    assert outcome_chiuso.sent == 0
    assert outcome_chiuso.off_hours == ["s"]


# --- lo sweep di ripresa ----------------------------------------------------


@dataclass
class _FakeQueueRepo:
    claimed: list[Any]
    deleted: list[Any]
    released: list[Any]
    claim_ok: bool = True

    def __call__(self, _session: Any) -> _FakeQueueRepo:
        return self

    async def claim(self, queue_id: Any, **_: Any) -> bool:
        if self.claim_ok:
            self.claimed.append(queue_id)
        return self.claim_ok

    async def delete(self, queue_id: Any) -> None:
        self.deleted.append(queue_id)

    async def release(self, queue_id: Any) -> None:
        self.released.append(queue_id)


class _FakeAnalytics:
    def __init__(self, sink: list[dict]) -> None:
        self._sink = sink

    def __call__(self, _session: Any) -> _FakeAnalytics:
        return self

    async def emit(self, **kwargs: Any) -> None:
        self._sink.append(kwargs)


class _FakeRedis:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def enqueue_job(self, name: str, **kwargs: Any) -> None:
        self.jobs.append({"name": name, **kwargs})


class _NullSession:
    async def __aenter__(self) -> Any:
        return SimpleNamespace()

    async def __aexit__(self, *_: Any) -> bool:
        return False


def _candidate(*, queued_at: datetime) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        merchant_id=uuid4(),
        automation_id=uuid4(),
        subject_type="lead",
        subject_id=uuid4(),
        node_keys=["s1"],
        episode_anchor="2026-09-01T02:00:00+00:00",
        dedup_key="offhours:a:b:s1",
        queued_at=queued_at,
    )


def _hours(*, is_open: bool) -> Any:
    """`resolve_automation_hours` ritorna None quando il vincolo è spento."""
    return SimpleNamespace(is_open=lambda _now=None: is_open)


@pytest.fixture
def sweep_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    queue = _FakeQueueRepo(claimed=[], deleted=[], released=[])
    events: list[dict] = []
    monkeypatch.setattr(sweep_mod, "AutomationHoursQueueRepository", queue)
    monkeypatch.setattr(sweep_mod, "AnalyticsRepository", _FakeAnalytics(events))
    monkeypatch.setattr(sweep_mod, "tenant_session", lambda _ctx: _NullSession())
    return {"queue": queue, "events": events}


async def test_sweep_non_riprende_se_ancora_chiuso(
    sweep_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merchant ancora chiuso → la riga resta in coda, intatta e non prenotata."""
    now = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    monkeypatch.setattr(
        sweep_mod,
        "resolve_automation_hours",
        _async_return(_hours(is_open=False)),
    )
    redis = _FakeRedis()
    cand = _candidate(queued_at=now - timedelta(hours=4))

    assert await _flush_one(cand, redis=redis, now=now) == "still_closed"
    assert redis.jobs == []
    assert sweep_env["queue"].claimed == []
    assert sweep_env["queue"].deleted == []


async def test_sweep_riaccoda_il_run_sui_nodi_sospesi(
    sweep_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Riaperto → `automation_run` con `start_keys` = i nodi sospesi.

    Si riaccoda il run, non si spedisce da qui: il testo lo ricalcola la
    lavagnetta alla consegna (ADR 0014) e la guardia d'episodio di ADR 0015
    riparte, così una cadenza il cui lead ha risposto stanotte si spegne invece
    di insistere.
    """
    now = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(
        sweep_mod,
        "resolve_automation_hours",
        _async_return(_hours(is_open=True)),
    )
    redis = _FakeRedis()
    cand = _candidate(queued_at=now - timedelta(hours=10))

    assert await _flush_one(cand, redis=redis, now=now) == "resumed"
    assert len(redis.jobs) == 1
    job = redis.jobs[0]
    assert job["name"] == "automation_run"
    assert job["start_keys"] == ["s1"]
    assert job["automation_id"] == str(cand.automation_id)
    assert job["episode_anchor"] == cand.episode_anchor
    # Dedup derivata dall'id della riga: stabile fra i ritentativi della stessa
    # riga, diversa alla prossima chiusura — riusare `dedup_key` farebbe
    # scartare in silenzio la ripresa della settimana dopo.
    assert job["dedup"] == f"offhours-resume:{cand.id}"
    assert sweep_env["queue"].deleted == [cand.id]


async def test_sweep_consegna_se_il_merchant_ha_spento_il_vincolo(
    sweep_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`apply_to_automations` spento durante la chiusura → la coda va consegnata.

    Altrimenti chi disattiva il vincolo si ritrova dei messaggi trattenuti per
    sempre da una regola che non ha più.
    """
    now = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    monkeypatch.setattr(
        sweep_mod,
        "resolve_automation_hours",
        _async_return(None),
    )
    redis = _FakeRedis()
    cand = _candidate(queued_at=now - timedelta(hours=4))

    assert await _flush_one(cand, redis=redis, now=now) == "resumed"
    assert len(redis.jobs) == 1


async def test_sweep_lascia_cadere_una_riga_troppo_vecchia(
    sweep_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oltre il tetto d'attesa la riga cade, e la caduta viene registrata.

    Un'agenda mai riaperta terrebbe la riga candidata a ogni passata per sempre
    e, col cap della scansione, affamerebbe le attese vere.
    """
    now = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(
        sweep_mod,
        "resolve_automation_hours",
        _async_return(_hours(is_open=True)),
    )
    redis = _FakeRedis()
    cand = _candidate(queued_at=now - timedelta(days=15))

    assert await _flush_one(cand, redis=redis, now=now) == "expired"
    assert redis.jobs == [], "scaduta: non si invia più"
    assert sweep_env["queue"].deleted == [cand.id]
    assert [e["event_type"] for e in sweep_env["events"]] == ["automation.send_dropped"]
    assert sweep_env["events"][0]["properties"]["reason"] == "never_reopened"


async def test_sweep_cede_il_passo_se_un_altra_passata_ha_gia_preso_la_riga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Senza claim, due passate sovrapposte = due volte lo stesso messaggio."""
    queue = _FakeQueueRepo(claimed=[], deleted=[], released=[], claim_ok=False)
    monkeypatch.setattr(sweep_mod, "AutomationHoursQueueRepository", queue)
    monkeypatch.setattr(sweep_mod, "AnalyticsRepository", _FakeAnalytics([]))
    monkeypatch.setattr(sweep_mod, "tenant_session", lambda _ctx: _NullSession())
    now = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(
        sweep_mod,
        "resolve_automation_hours",
        _async_return(_hours(is_open=True)),
    )
    redis = _FakeRedis()

    assert (
        await _flush_one(_candidate(queued_at=now - timedelta(hours=8)), redis=redis, now=now)
        == "already_claimed"
    )
    assert redis.jobs == []
    assert queue.deleted == []


async def test_sweep_rilascia_il_claim_se_il_riaccodamento_fallisce(
    sweep_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis giù a metà: la riga resta in coda e il claim viene liberato subito.

    Senza il rilascio il ritentativo resterebbe fermo fino alla scadenza del
    claim; senza la riga, l'attesa verrebbe consumata in silenzio.
    """
    now = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(
        sweep_mod,
        "resolve_automation_hours",
        _async_return(_hours(is_open=True)),
    )

    class _BrokenRedis:
        async def enqueue_job(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("redis giù")

    cand = _candidate(queued_at=now - timedelta(hours=8))
    with pytest.raises(RuntimeError):
        await _flush_one(cand, redis=_BrokenRedis(), now=now)

    assert sweep_env["queue"].released == [cand.id]
    assert sweep_env["queue"].deleted == [], "la riga resta in coda per il prossimo tick"


def _async_return(value: Any) -> Any:
    async def _inner(*_: Any, **__: Any) -> Any:
        return value

    return _inner


# --- l'ancora d'episodio sulla riga collassata ------------------------------


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _RecordingSession:
    """Cattura lo statement passato a execute, senza toccare un database."""

    def __init__(self, *, inserted: bool) -> None:
        self.statements: list[Any] = []
        self._inserted = inserted

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(stmt)
        return _FakeResult(SimpleNamespace(id=uuid4(), inserted=self._inserted))


async def test_enqueue_in_conflitto_aggiorna_l_ancora_e_non_riemette() -> None:
    """Il conflitto aggiorna `episode_anchor` invece di ignorare la scrittura.

    Regressione difesa: tenendo la PRIMA ancora si perdeva un invio legittimo in
    silenzio. Lead muto alle 02:00 → in coda con ancora 02:00; risponde alle
    04:00 (episodio chiuso, giustamente); torna muto e alle 06:00 ne parte uno
    nuovo, che collide. Con `DO NOTHING` la riga restava a 02:00, e alle 09:00
    la guardia d'episodio confrontava l'inbound delle 04:00 con l'ancora delle
    02:00, concludeva "episodio finito" e non mandava nulla.

    E il ritorno deve dire False sull'aggiornamento, altrimenti l'evento
    `automation.send_queued` verrebbe emesso due volte per un messaggio solo.
    """
    from db.repositories.automation import AutomationHoursQueueRepository

    session = _RecordingSession(inserted=False)
    creato = await AutomationHoursQueueRepository(session).enqueue(
        tenant_id=uuid4(),
        merchant_id=uuid4(),
        automation_id=uuid4(),
        subject_type="lead",
        subject_id=uuid4(),
        node_keys=["s1"],
        episode_anchor="2026-09-01T06:00:00+00:00",
        dedup_key="offhours:a:b:s1",
    )

    assert creato is False, "un aggiornamento non è un nuovo accodamento"
    from sqlalchemy.dialects import postgresql

    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql and "DO UPDATE" in sql, "il conflitto deve aggiornare"
    assert "episode_anchor" in sql.split("DO UPDATE", 1)[1], "…e aggiornare proprio l'ancora"


async def test_enqueue_nuovo_segnala_l_inserimento() -> None:
    """Inserimento vero → True, così l'evento parte una volta sola."""
    from db.repositories.automation import AutomationHoursQueueRepository

    session = _RecordingSession(inserted=True)
    creato = await AutomationHoursQueueRepository(session).enqueue(
        tenant_id=uuid4(),
        merchant_id=uuid4(),
        automation_id=uuid4(),
        subject_type="lead",
        subject_id=uuid4(),
        node_keys=["s1"],
        episode_anchor=None,
        dedup_key="offhours:a:b:s1",
    )
    assert creato is True
