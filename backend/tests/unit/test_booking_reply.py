"""`compose_booking_reply` — natural-language booking outcome (unit, no DB/network).

Mirrors `test_crm_summary.py`'s stub pattern. The property that matters: this is
fail-open. If the model errs, times out, or returns nothing usable, the function
returns `None` and the caller (`actions/booking.py`) falls back to the fixed
Italian template — a booking outcome must never be left unsent.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from ai_core.booking_reply import compose_booking_reply


class _FakeClient:
    def __init__(self, *, reply: str = "Perfetto, confermato!", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.chiamate: list[Any] = []

    async def complete(self, *, messages: Any, max_tokens: int | None = None, **kw: Any) -> Any:
        self.chiamate.append(messages)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            content=self.reply, model="gpt-5-nano", tokens_in=10, tokens_out=5, latency_ms=1, raw={}
        )


class _FakeRouter:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client
        self.richieste: list[Any] = []

    async def select(self, req: Any) -> Any:
        self.richieste.append(req)
        return self.client


async def _componi(router: Any, **kw: Any) -> str | None:
    return await compose_booking_reply(
        router,
        merchant_id=uuid4(),
        tenant_id=uuid4(),
        situation=kw.pop("situation", "Prenotazione confermata."),
        facts=kw.pop("facts", "Orario confermato: lunedì alle 10:00."),
        **kw,
    )


async def test_usa_il_ramo_economico_del_router() -> None:
    """`purpose="sentiment"` è il ramo gpt-5-nano — non deve mai finire su gpt-5.2
    per via dei trigger di escalation (un compose non è un ragionamento)."""
    router = _FakeRouter(_FakeClient())

    testo = await _componi(router)

    assert testo == "Perfetto, confermato!"
    assert router.richieste[0].purpose == "sentiment"
    assert router.richieste[0].lead_score == 0


async def test_errore_del_modello_non_solleva_e_torna_none() -> None:
    router = _FakeRouter(_FakeClient(error=RuntimeError("429 rate limit")))

    assert await _componi(router) is None


async def test_risposta_vuota_diventa_none() -> None:
    router = _FakeRouter(_FakeClient(reply="   "))

    assert await _componi(router) is None


async def test_situazione_e_dati_reali_arrivano_al_modello() -> None:
    client = _FakeClient()
    router = _FakeRouter(client)

    await _componi(
        router,
        situation="Il cliente vuole prenotare ma non ha indicato un orario.",
        facts="Orari liberi: venerdì alle 10:45; venerdì alle 11:00.",
    )

    user_msg = client.chiamate[0][1].content
    assert "Il cliente vuole prenotare ma non ha indicato un orario." in user_msg
    assert "venerdì alle 10:45" in user_msg


async def test_il_system_prompt_vieta_di_inventare_orari() -> None:
    client = _FakeClient()
    router = _FakeRouter(client)

    await _componi(router)

    system = client.chiamate[0][0].content
    assert "non inventare" in system.lower()
