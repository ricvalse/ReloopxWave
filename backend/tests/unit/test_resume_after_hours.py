"""Sweep di ripresa: risponde alla riapertura, tace se c'è già un umano.

Il caso che dà il nome al requisito è
`test_skips_and_clears_when_an_operator_already_replied`: il bot non deve
scavalcare un operatore che ha risolto la questione durante la chiusura.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from workers.scheduler import resume_after_hours as mod

from db import OffHoursPendingCandidate

NOW = datetime(2026, 1, 16, 8, 30, tzinfo=UTC)  # venerdì 09:30 a Roma → aperto


def _cand(**over: Any) -> OffHoursPendingCandidate:
    base: dict[str, Any] = {
        "conversation_id": uuid.uuid4(),
        "merchant_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "wa_phone_number_id": "pn-1",
        "wa_contact_phone": "+393330000001",
        "pending_since": NOW - timedelta(hours=10),  # ieri sera
    }
    base.update(over)
    return OffHoursPendingCandidate(**base)


class _Msg:
    def __init__(self, content: str, *, wa_id: str, created_at: datetime) -> None:
        self.content = content
        self.wa_message_id = wa_id
        self.created_at = created_at


class _Hours:
    def __init__(self, *, open_now: bool) -> None:
        self._open = open_now

    def is_open(self, moment: Any = None) -> bool:
        return self._open


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidates: list[OffHoursPendingCandidate],
    open_now: bool = True,
    human_replied: bool = False,
    pending_msgs: list[_Msg] | None = None,
    handled: bool = True,
    reason: str | None = None,
    cleared: list[Any] | None = None,
    sent: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    claimed: set[Any] | None = None,
    released: list[Any] | None = None,
) -> dict[str, Any]:
    if claimed is None:
        claimed = set()

    if pending_msgs is None:
        pending_msgs = [
            _Msg("quanto costa il taglio?", wa_id="wa-1", created_at=NOW - timedelta(hours=10)),
            _Msg("e siete aperti sabato?", wa_id="wa-2", created_at=NOW - timedelta(hours=9)),
        ]

    @asynccontextmanager
    async def fake_session_scope() -> Any:
        yield object()

    @asynccontextmanager
    async def fake_tenant_session(ctx: Any) -> Any:
        yield object()

    monkeypatch.setattr(mod, "session_scope", fake_session_scope)
    monkeypatch.setattr(mod, "tenant_session", fake_tenant_session)

    class FakeConvRepo:
        def __init__(self, session: Any) -> None: ...

        async def list_off_hours_pending(self, *, limit: int = 500) -> list[Any]:
            return candidates

        async def human_replied_since(self, conversation_id: Any, since: Any) -> bool:
            return human_replied

        async def clear_off_hours_pending(self, conversation_id: Any) -> None:
            if cleared is not None:
                cleared.append(conversation_id)

        async def claim_off_hours_resume(
            self, conversation_id: Any, pending_since: Any, *, stale_after_minutes: int = 15
        ) -> bool:
            # Il claim è atomico a DB: qui lo simuliamo con un insieme, così
            # una seconda passata sulla stessa riga se lo vede negare.
            if conversation_id in claimed:
                return False
            claimed.add(conversation_id)
            return True

        async def release_off_hours_resume(self, conversation_id: Any) -> None:
            claimed.discard(conversation_id)
            if released is not None:
                released.append(conversation_id)

    class FakeMsgRepo:
        def __init__(self, session: Any) -> None: ...

        async def list_inbound_since(
            self, conversation_id: Any, since: Any, *, limit: int = 20
        ) -> list[Any]:
            return pending_msgs

    class FakeAnalytics:
        def __init__(self, session: Any) -> None: ...

        async def emit(self, **kw: Any) -> None:
            if events is not None:
                events.append(kw)

    async def fake_resolve(session: Any, merchant_id: Any) -> Any:
        return _Hours(open_now=open_now)

    monkeypatch.setattr(mod, "ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(mod, "MessageRepository", FakeMsgRepo)
    monkeypatch.setattr(mod, "AnalyticsRepository", FakeAnalytics)
    monkeypatch.setattr(mod, "resolve_response_hours", fake_resolve)

    class FakeService:
        async def generate_and_send_reply(self, **kw: Any) -> Any:
            if sent is not None:
                sent.append(kw)
            return type("R", (), {"handled": handled, "reason": reason})()

    class FakeRuntime:
        conversation_service = FakeService()

    return {"runtime": FakeRuntime()}


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return NOW

    monkeypatch.setattr(mod, "datetime", FakeDatetime)


@pytest.mark.asyncio
async def test_resumes_a_pending_conversation_at_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []
    cleared: list[Any] = []
    cand = _cand()
    ctx = _patch(monkeypatch, candidates=[cand], sent=sent, cleared=cleared)
    _freeze(monkeypatch)

    out = await mod.resume_after_hours(ctx)

    assert out["resumed"] == 1
    assert cleared == [cand.conversation_id]
    # Risponde a TUTTA la raffica notturna, non solo all'ultimo messaggio.
    assert sent[0]["text"] == "quanto costa il taglio?\ne siete aperti sabato?"
    assert sent[0]["resumed_after_hours"] is True
    assert sent[0]["exclude_wa_message_ids"] == ["wa-1", "wa-2"]


@pytest.mark.asyncio
async def test_skips_and_clears_when_an_operator_already_replied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il requisito: se ha già risposto un operatore, il bot tace.

    Copre il buco dell'eco dal telefono, che mette solo una pausa di due ore:
    dopo una notte è scaduta, e senza questo controllo il bot parlerebbe sopra
    l'operatore.
    """
    sent: list[dict[str, Any]] = []
    cleared: list[Any] = []
    cand = _cand()
    ctx = _patch(monkeypatch, candidates=[cand], human_replied=True, sent=sent, cleared=cleared)
    _freeze(monkeypatch)

    out = await mod.resume_after_hours(ctx)

    assert out["human_replied"] == 1
    assert out["resumed"] == 0
    assert sent == []  # il bot NON ha parlato
    assert cleared == [cand.conversation_id]  # e non è più in attesa


@pytest.mark.asyncio
async def test_still_closed_is_a_noop_and_keeps_the_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []
    cleared: list[Any] = []
    ctx = _patch(monkeypatch, candidates=[_cand()], open_now=False, sent=sent, cleared=cleared)
    _freeze(monkeypatch)

    out = await mod.resume_after_hours(ctx)

    assert out["still_closed"] == 1
    assert sent == []
    assert cleared == []  # resta in attesa per il prossimo tick


@pytest.mark.asyncio
async def test_marker_survives_a_failed_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un invio non andato a buon fine non deve consumare l'attesa in silenzio."""
    cleared: list[Any] = []
    ctx = _patch(
        monkeypatch,
        candidates=[_cand()],
        handled=False,
        reason="auto_reply_off",
        cleared=cleared,
    )
    _freeze(monkeypatch)

    out = await mod.resume_after_hours(ctx)

    assert out["resumed"] == 0
    assert cleared == []


@pytest.mark.asyncio
async def test_drops_the_marker_when_the_24h_window_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Venerdì sera → lunedì mattina: il testo libero non è più permesso."""
    sent: list[dict[str, Any]] = []
    cleared: list[Any] = []
    events: list[dict[str, Any]] = []
    old = NOW - timedelta(hours=58)
    ctx = _patch(
        monkeypatch,
        candidates=[_cand(pending_since=old)],
        pending_msgs=[_Msg("ci siete?", wa_id="wa-9", created_at=old)],
        sent=sent,
        cleared=cleared,
        events=events,
    )
    _freeze(monkeypatch)

    out = await mod.resume_after_hours(ctx)

    assert out["expired"] == 1
    assert sent == []
    assert cleared  # non resta appeso per sempre
    assert events[0]["event_type"] == "conversation.resume_expired"
    assert events[0]["properties"]["reason"] == "whatsapp_24h_window_closed"


@pytest.mark.asyncio
async def test_orphan_marker_without_messages_is_cleaned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared: list[Any] = []
    sent: list[dict[str, Any]] = []
    ctx = _patch(monkeypatch, candidates=[_cand()], pending_msgs=[], cleared=cleared, sent=sent)
    _freeze(monkeypatch)

    out = await mod.resume_after_hours(ctx)

    assert out["expired"] == 1
    assert sent == []
    assert cleared


@pytest.mark.asyncio
async def test_very_old_marker_is_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agenda configurata male: l'attesa non può restare candidata per sempre."""
    cleared: list[Any] = []
    ctx = _patch(
        monkeypatch,
        candidates=[_cand(pending_since=NOW - timedelta(days=20))],
        open_now=False,
        cleared=cleared,
    )
    _freeze(monkeypatch)

    out = await mod.resume_after_hours(ctx)

    assert out["expired"] == 1
    assert cleared


@pytest.mark.asyncio
async def test_one_bad_candidate_does_not_stop_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _cand()
    ctx = _patch(monkeypatch, candidates=[_cand(), good])
    _freeze(monkeypatch)

    calls = {"n": 0}
    original = mod._resume_one

    async def flaky(cand: Any, *, runtime: Any, now: Any) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await original(cand, runtime=runtime, now=now)

    monkeypatch.setattr(mod, "_resume_one", flaky)

    out = await mod.resume_after_hours(ctx)

    assert out["failed"] == 1
    assert out["resumed"] == 1  # il secondo è stato comunque servito


@pytest.mark.asyncio
async def test_two_overlapping_sweeps_reply_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Due passate sovrapposte devono produrre UNA risposta, non due.

    Scenario reale: il lunedì dopo un fine settimana lo sweep ha centinaia di
    conversazioni da riprendere, una chiamata al modello ciascuna, e il giro
    dura più dei cinque minuti che separano un tick dal successivo. Senza il
    claim la seconda passata ritrova le stesse righe ancora marcate e il
    cliente riceve due risposte alla riapertura.
    """
    sent: list[dict[str, Any]] = []
    claimed: set[Any] = set()
    cand = _cand()

    # Prima passata: prende in carico ma non arriva a ripulire il marcatore
    # (è ancora "in volo" quando parte la seconda).
    ctx = _patch(monkeypatch, candidates=[cand], sent=sent, claimed=claimed, cleared=None)
    _freeze(monkeypatch)
    first = await mod.resume_after_hours(ctx)

    # Seconda passata sulla STESSA riga, claim ancora acceso.
    ctx2 = _patch(monkeypatch, candidates=[cand], sent=sent, claimed=claimed, cleared=None)
    _freeze(monkeypatch)
    second = await mod.resume_after_hours(ctx2)

    assert first["resumed"] == 1
    assert second["resumed"] == 0
    assert second["already_claimed"] == 1
    assert len(sent) == 1  # una sola risposta, non due


@pytest.mark.asyncio
async def test_claim_is_released_when_the_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un invio fallito rilascia il claim: il tick dopo deve poter riprovare.

    Senza il rilascio la conversazione resterebbe bloccata fino alla scadenza
    del claim, cioè un quarto d'ora di silenzio in più su una risposta che
    avevamo promesso.
    """
    claimed: set[Any] = set()
    released: list[Any] = []
    cleared: list[Any] = []
    cand = _cand()
    ctx = _patch(
        monkeypatch,
        candidates=[cand],
        handled=False,
        reason="auto_reply_off",
        claimed=claimed,
        released=released,
        cleared=cleared,
    )
    _freeze(monkeypatch)

    await mod.resume_after_hours(ctx)

    assert released == [cand.conversation_id]
    assert claimed == set()  # libero per il prossimo tick
    assert cleared == []  # ma il marcatore resta: la domanda è ancora in attesa


@pytest.mark.asyncio
async def test_cheap_skips_do_not_burn_a_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chi esce prima di inviare non deve lasciare un claim da rilasciare."""
    claimed: set[Any] = set()
    ctx = _patch(monkeypatch, candidates=[_cand()], open_now=False, claimed=claimed)
    _freeze(monkeypatch)

    await mod.resume_after_hours(ctx)

    assert claimed == set()
