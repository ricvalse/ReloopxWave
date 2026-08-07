"""L'invariante di ordinamento fra chiusura per inattività e follow-up UC-03.

Il bug che questi test bloccano: `close_idle_conversations` chiudeva ogni
conversazione ferma da `conversation.idle_close_minutes` (120), e
`list_reminder_candidates` guarda solo le conversazioni `active`. Con il default
del nodo trigger anch'esso a 120 minuti, la chiusura arrivava prima e qualunque
automazione "nessuna risposta" con ritardo ≥ 120 non partiva mai.

I test UC-03 esistenti non l'hanno intercettato perché stubbano
`_scan_candidates`: saltano esattamente il livello in cui vivevano sia il filtro
sullo stato sia il pavimento di 30 minuti. Qui si esercita quel livello.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from workers.scheduler import close_conversations, no_answer

from db.repositories.conversation import ConversationRepository


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _FakeResult:
        return self

    def mappings(self) -> list[Any]:
        return self._rows


class _RecordingSession:
    """Sessione minima: registra le UPDATE e serve righe finte alle SELECT."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.updates: list[Any] = []

    async def execute(self, stmt: Any, *args: Any, **kw: Any) -> _FakeResult:
        compiled = str(stmt).strip().upper()
        if compiled.startswith("UPDATE"):
            self.updates.append(stmt)
            return _FakeResult([])
        return _FakeResult(self._rows)


def _row(minutes_idle: int, merchant_id: uuid.UUID) -> tuple[Any, ...]:
    now = datetime.now(tz=UTC)
    return (uuid.uuid4(), merchant_id, now - timedelta(minutes=minutes_idle))


async def test_close_skips_conversation_still_awaiting_followup() -> None:
    """Una conversazione ferma da 130 minuti non si chiude se il merchant ha un
    follow-up configurato a 180: chiuderla lo cancellerebbe."""
    merchant = uuid.uuid4()
    session = _RecordingSession([_row(130, merchant)])
    repo = ConversationRepository(session)

    closed = await repo.close_idle_active(
        min_idle_minutes=120,
        followup_floor_by_merchant={merchant: 180 + 30},
    )

    assert closed == []
    assert session.updates == []  # nessuna chiusura, nessun reset di profilo


async def test_close_proceeds_once_followup_window_elapsed() -> None:
    """Passata la finestra di follow-up (più il margine) la chiusura riprende."""
    merchant = uuid.uuid4()
    session = _RecordingSession([_row(400, merchant)])
    repo = ConversationRepository(session)

    closed = await repo.close_idle_active(
        min_idle_minutes=120,
        followup_floor_by_merchant={merchant: 180 + 30},
    )

    assert len(closed) == 1


async def test_close_unaffected_for_merchant_without_automation() -> None:
    """Chi non ha automazioni no_answer non ha floor: vale la soglia di sempre."""
    merchant = uuid.uuid4()
    session = _RecordingSession([_row(130, merchant)])
    repo = ConversationRepository(session)

    closed = await repo.close_idle_active(min_idle_minutes=120, followup_floor_by_merchant={})

    assert len(closed) == 1


async def test_close_floor_covers_the_emitter_tick_latency() -> None:
    """Il margine non è decorativo: l'emettitore UC-03 gira ogni 15 minuti, quindi
    una conversazione che matura subito dopo un tick viene vista fino a un tick
    più tardi. Chiudere dentro quella finestra la perderebbe comunque."""
    assert close_conversations._FOLLOWUP_GRACE_MINUTES >= 15


async def test_scan_floor_honours_short_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un ritardo di 10 minuti deve produrre un pavimento di scansione di 10, non
    di 30: la costante `_MIN_IDLE_MINUTES = 30` sovrascriveva silenziosamente
    qualunque valore più corto configurato sul nodo trigger."""
    seen: dict[str, int] = {}

    async def fake_delays() -> dict[Any, list[int]]:
        return {uuid.uuid4(): [10]}

    async def fake_scan(min_idle_minutes: int) -> list[Any]:
        seen["floor"] = min_idle_minutes
        return []

    monkeypatch.setattr(no_answer, "_enabled_delays", fake_delays)
    monkeypatch.setattr(no_answer, "_scan_candidates", fake_scan)

    await no_answer.followup_no_answer({})

    assert seen["floor"] == 10


async def test_scan_skipped_entirely_without_enabled_automations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Senza nessuna automazione no_answer attiva sulla piattaforma non ha senso
    scandire `conversations` ogni 15 minuti."""
    scanned = False

    async def fake_delays() -> dict[Any, list[int]]:
        return {}

    async def fake_scan(min_idle_minutes: int) -> list[Any]:
        nonlocal scanned
        scanned = True
        return []

    monkeypatch.setattr(no_answer, "_enabled_delays", fake_delays)
    monkeypatch.setattr(no_answer, "_scan_candidates", fake_scan)

    out = await no_answer.followup_no_answer({})

    assert scanned is False
    assert out["skipped"] == "no_enabled_automations"


async def test_scan_floor_is_the_shortest_configured_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il pavimento è il minimo globale; ogni candidato viene poi rifiltrato con la
    soglia del proprio merchant in `_maybe_emit`."""
    seen: dict[str, int] = {}

    async def fake_delays() -> dict[Any, list[int]]:
        return {uuid.uuid4(): [240], uuid.uuid4(): [45, 600]}

    async def fake_scan(min_idle_minutes: int) -> list[Any]:
        seen["floor"] = min_idle_minutes
        return []

    monkeypatch.setattr(no_answer, "_enabled_delays", fake_delays)
    monkeypatch.setattr(no_answer, "_scan_candidates", fake_scan)

    await no_answer.followup_no_answer({})

    assert seen["floor"] == 45


def test_close_floor_is_the_longest_delay_per_merchant() -> None:
    """Lo sweep di chiusura usa il **massimo** per merchant, non il minimo: deve
    tenere aperta la conversazione fino all'ultimo follow-up possibile."""
    merchant = uuid.uuid4()
    delays = {merchant: [60, 300, 120]}
    floors = {m: max(v) + close_conversations._FOLLOWUP_GRACE_MINUTES for m, v in delays.items()}
    assert floors[merchant] == 300 + close_conversations._FOLLOWUP_GRACE_MINUTES


def test_default_delay_is_shared_by_both_sweeps() -> None:
    """Emettitore e sweep di chiusura devono attribuire lo stesso ritardo a un nodo
    trigger che non lo dichiara, altrimenti tornano a divergere."""
    assert close_conversations.DEFAULT_DELAY_MINUTES is no_answer.DEFAULT_DELAY_MINUTES


def test_delay_falls_back_to_the_shared_default() -> None:
    trigger = SimpleNamespace(kind="trigger", type="no_answer", config={})
    flow = SimpleNamespace(id=uuid.uuid4(), nodes=[trigger], edges=[])
    assert no_answer._delay_minutes(flow) == no_answer.DEFAULT_DELAY_MINUTES


def test_delay_is_read_per_automation_not_collapsed() -> None:
    """Il ritardo appartiene alla singola automazione (ADR 0027).

    Era il `min()` fra tutte quelle del merchant: con due automazioni a 60 e 240
    minuti, l'emissione avveniva a 60 per entrambe e l'ancora si bruciava lì,
    quindi quella da 240 non partiva mai.
    """

    def _flow(delay: int) -> Any:
        return SimpleNamespace(
            id=uuid.uuid4(),
            nodes=[
                SimpleNamespace(kind="trigger", type="no_answer", config={"delay_minutes": delay})
            ],
            edges=[],
        )

    breve, lunga = _flow(60), _flow(240)
    assert no_answer._delay_minutes(breve) == 60
    assert no_answer._delay_minutes(lunga) == 240
