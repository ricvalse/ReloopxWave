"""Nodo `set_lead_field`: la nota sul contatto GHL che accompagna il tag (unit).

Il tag dice *cosa* è stato deciso, la nota *perché* e *da parte di chi*. Qui si
coprono le proprietà che rendono la nota sicura da accendere in produzione:

  - i grafi già salvati non hanno la chiave `ghl_note` → nessuna nota, nessun
    cambio di comportamento;
  - accesa senza testo → nota automatica (tag, automazione, lead, punteggio);
  - accesa con testo → interpolato con le stesse variabili del testo libero;
  - best-effort: quando la nota parte il tag è **già scritto**, quindi una nota
    che fallisce non deve far risultare fallito il tag;
  - il client GHL viene chiuso su ogni percorso, ramo "tag vuoto" compreso — è
    il motivo per cui quel ramo è stato portato dentro il try/finally.

Complementa test_notify_slack_node.py (stesso stile: nodo d'azione con
`IntegrationRepository` e client esterno finti, niente DB e niente rete).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from workers.automation import engine
from workers.automation.engine import RunContext

from ai_core.automations import GHL_NOTE_MAX_LEN


def _run_ctx(**over: Any) -> RunContext:
    base: dict[str, Any] = {
        "phone": "393331112233",
        "wa_phone_number_id": "pnid",
        "within_window": True,
        "score": 72,
        "temperature": "hot",
        "name": "Mario Rossi",
        "last_message": "quanto costa?",
        "lead_id": uuid4(),
        "conversation_id": uuid4(),
        "tenant_id": uuid4(),
        "merchant_id": uuid4(),
        "automation_name": "Recruiting DM",
    }
    base.update(over)
    return RunContext(**base)


def _settings() -> Any:
    return SimpleNamespace(
        integrations_kek_base64="k", ghl_client_id="cid", ghl_client_secret="sec"
    )


def _node() -> Any:
    return SimpleNamespace(node_key="n1", kind="action", type="set_lead_field", config={})


def _patch_ghl(
    monkeypatch: pytest.MonkeyPatch,
    *,
    upsert_result: dict[str, Any] | None = None,
    note_error: Exception | None = None,
    integrazione: Any = "default",
) -> list[Any]:
    """Sostituisce repo integrazioni e client GHL. Ritorna la lista dei client creati."""
    creati: list[Any] = []
    ghl = (
        SimpleNamespace(access_token="at", refresh_token="rt", expires_at=0, location_id="loc")
        if integrazione == "default"
        else integrazione
    )

    class FakeIntegRepo:
        def __init__(self, session: Any, *, kek_base64: str) -> None: ...

        async def resolve_ghl(self, merchant_id: Any) -> Any:
            return ghl

    class FakeClient:
        def __init__(self, **kw: Any) -> None:
            self.upserts: list[dict[str, Any]] = []
            self.notes: list[tuple[str, str]] = []
            self.closed = 0
            creati.append(self)

        async def upsert_contact(self, payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
            self.upserts.append(payload)
            return {"contact": {"id": "CT-1"}} if upsert_result is None else upsert_result

        async def add_contact_note(self, contact_id: str, *, body: str) -> dict[str, Any]:
            if note_error is not None:
                raise note_error
            self.notes.append((contact_id, body))
            return {"id": "NOTE-1"}

        async def close(self) -> None:
            self.closed += 1

    monkeypatch.setattr(engine, "IntegrationRepository", FakeIntegRepo)
    monkeypatch.setattr(engine, "GHLClient", FakeClient)
    return creati


async def _scrivi_tag(monkeypatch: pytest.MonkeyPatch, cfg: dict[str, Any], **over: Any) -> Any:
    creati = _patch_ghl(monkeypatch, **over.pop("ghl_kwargs", {}))
    await engine._set_ghl_contact_field(
        _node(),
        cfg,
        over.pop("run_ctx", None) or _run_ctx(),
        session=over.pop("session", None) or object(),
        settings=_settings(),
        field=cfg.get("field", "tag"),
        router=over.pop("router", None),
    )
    return creati[0] if creati else None


# --- retrocompatibilità ----------------------------------------------------


async def test_grafo_esistente_senza_chiave_non_scrive_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La config salvata prima di questa feature non ha `ghl_note`: il tag va, la nota no."""
    client = await _scrivi_tag(monkeypatch, {"field": "tag", "value": "VIP", "ghl_sync": True})

    assert client.upserts == [{"phone": "393331112233", "tags": ["VIP"]}]
    assert client.notes == []
    assert client.closed == 1


# --- nota automatica -------------------------------------------------------


async def test_nota_automatica_dice_cosa_chi_e_su_chi(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _scrivi_tag(
        monkeypatch, {"field": "tag", "value": "VIP", "ghl_sync": True, "ghl_note": True}
    )

    assert len(client.notes) == 1
    contact_id, body = client.notes[0]
    assert contact_id == "CT-1"
    # Il tag applicato, l'automazione che l'ha deciso e il lead su cui è finito:
    # è la domanda a cui la nota deve rispondere aprendo il contatto su GHL.
    assert "VIP" in body
    assert "Recruiting DM" in body
    assert "Mario Rossi" in body
    assert "393331112233" in body
    assert "72" in body


async def test_nota_automatica_ripiega_sul_trigger_senza_nome_automazione(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await _scrivi_tag(
        monkeypatch,
        {"field": "tag", "value": "VIP", "ghl_sync": True, "ghl_note": True},
        run_ctx=_run_ctx(automation_name="", trigger_type="lead.dormant"),
    )

    assert "lead.dormant" in client.notes[0][1]


async def test_nota_automatica_su_campo_personalizzato(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "custom_field",
            "key": "citta",
            "value": "Milano",
            "ghl_sync": True,
            "ghl_note": True,
        },
    )

    body = client.notes[0][1]
    assert "citta" in body
    assert "Milano" in body


# --- nota con testo del merchant -------------------------------------------


async def test_testo_personalizzato_usa_le_variabili_del_testo_libero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stessa sintassi puntata del nodo `send`: una sola cosa da imparare."""
    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_text": "Tag per {{lead.first_name}} ({{contact.phone}})",
        },
    )

    assert client.notes[0][1] == "Tag per Mario (393331112233)"


async def test_testo_personalizzato_troncato_al_tetto_di_ghl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il validatore rifiuta al salvataggio, il motore tronca lo stesso: una nota
    accorciata è meglio di un 400 e di una nota persa."""
    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_text": "x" * (GHL_NOTE_MAX_LEN + 500),
        },
    )

    assert len(client.notes[0][1]) == GHL_NOTE_MAX_LEN


async def test_testo_che_si_svuota_dopo_l_interpolazione_non_scrive_nota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_text": "{{lead.sconosciuta}}",
        },
    )

    assert client.upserts  # il tag è comunque passato
    assert client.notes == []


# --- robustezza ------------------------------------------------------------


async def test_nota_fallita_non_fa_fallire_il_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quando la nota parte il tag è già scritto: un errore qui non può annullarlo."""
    client = await _scrivi_tag(
        monkeypatch,
        {"field": "tag", "value": "VIP", "ghl_sync": True, "ghl_note": True},
        ghl_kwargs={"note_error": RuntimeError("GHL 500")},
    )

    assert client.upserts == [{"phone": "393331112233", "tags": ["VIP"]}]
    assert client.closed == 1


async def test_upsert_senza_id_contatto_non_scrive_nota(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _scrivi_tag(
        monkeypatch,
        {"field": "tag", "value": "VIP", "ghl_sync": True, "ghl_note": True},
        ghl_kwargs={"upsert_result": {}},
    )

    assert client.notes == []
    assert client.closed == 1


async def test_id_contatto_anche_in_forma_piatta(monkeypatch: pytest.MonkeyPatch) -> None:
    client = await _scrivi_tag(
        monkeypatch,
        {"field": "tag", "value": "VIP", "ghl_sync": True, "ghl_note": True},
        ghl_kwargs={"upsert_result": {"id": "CT-piatto"}},
    )

    assert client.notes[0][0] == "CT-piatto"


async def test_tag_vuoto_esce_senza_upsert_ma_chiude_il_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il ramo che prima chiudeva il client fuori dal try: con la nota in mezzo era
    la via più corta per lasciarsi un client aperto."""
    client = await _scrivi_tag(
        monkeypatch, {"field": "tag", "value": "   ", "ghl_sync": True, "ghl_note": True}
    )

    assert client.upserts == []
    assert client.notes == []
    assert client.closed == 1


# --- il gate a monte -------------------------------------------------------


async def test_senza_sincronizzazione_ghl_non_si_apre_nessun_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Senza `ghl_sync` non esiste un contatto su cui scrivere: né tag né nota."""
    creati = _patch_ghl(monkeypatch)
    cfg = {"field": "tag", "value": "VIP", "ghl_sync": False, "ghl_note": True}

    inviato = await engine._do_set_lead_field(
        _node(), cfg, _run_ctx(), session=object(), settings=_settings()
    )

    assert inviato is False
    assert creati == []


async def test_senza_integrazione_ghl_non_esplode(monkeypatch: pytest.MonkeyPatch) -> None:
    creati = _patch_ghl(monkeypatch, integrazione=None)

    await engine._set_ghl_contact_field(
        _node(),
        {"field": "tag", "value": "VIP", "ghl_sync": True, "ghl_note": True},
        _run_ctx(),
        session=object(),
        settings=_settings(),
        field="tag",
    )

    assert creati == []


# --- il contesto che la nota fotografa -------------------------------------


async def test_la_nota_riporta_il_punteggio_scritto_dal_nodo_precedente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un `score_delta` prima del tag: la nota deve dire il punteggio nuovo.

    Il delta finiva a DB ma non nel `run_ctx`, quindi ogni nodo a valle (questa
    nota, ma anche le condizioni su `lead_score` e i testi con `{{lead.score}}`)
    leggeva il valore di prima — un numero che a DB non esiste piu'.
    """
    scritti: list[int] = []

    class FakeLeadRepo:
        def __init__(self, session: Any) -> None: ...

        async def update_score(self, lead_id: Any, *, score: int, reasons: list[str]) -> None:
            scritti.append(score)

    monkeypatch.setattr(engine, "LeadRepository", FakeLeadRepo)
    creati = _patch_ghl(monkeypatch)
    rc = _run_ctx(score=50, temperature="warm")

    await engine._do_set_lead_field(
        _node(), {"field": "score_delta", "value": 35}, rc, session=object(), settings=_settings()
    )
    await engine._set_ghl_contact_field(
        _node(),
        {"field": "tag", "value": "VIP", "ghl_sync": True, "ghl_note": True},
        rc,
        session=object(),
        settings=_settings(),
        field="tag",
    )

    assert scritti == [85]
    assert (rc.score, rc.temperature) == (85, "hot")
    body = creati[0].notes[0][1]
    assert "85" in body
    assert "hot" in body


async def test_campo_personalizzato_senza_valore_non_scrive_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un nodo salvato prima di questa feature ha `value: ''`: la nota automatica
    non deve raccontare al merchant che il campo vale «None»."""
    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "custom_field",
            "key": "citta",
            "value": "",
            "ghl_sync": True,
            "ghl_note": True,
        },
    )

    body = client.notes[0][1]
    assert "None" not in body
    assert "citta" in body


# --- riassunto AI della conversazione --------------------------------------


class _FakeConv:
    """Riga `conversations` finta: serve solo per il campo `meta` (la cache)."""

    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}


class _SessionConConv:
    def __init__(self, conv: Any) -> None:
        self._conv = conv

    async def get(self, _model: Any, _id: Any) -> Any:
        return self._conv


class _FakeRouterRiassunto:
    def __init__(self, *, reply: str = "Il lead chiede il prezzo.", error: Any = None) -> None:
        self.reply = reply
        self.error = error
        self.chiamate = 0

    async def select(self, req: Any) -> Any:
        router = self

        class Client:
            async def complete(self, **kw: Any) -> Any:
                router.chiamate += 1
                if router.error is not None:
                    raise router.error
                return SimpleNamespace(
                    content=router.reply,
                    model="gpt-5-nano",
                    tokens_in=10,
                    tokens_out=5,
                    latency_ms=1,
                    raw={},
                )

        return Client()


def _patch_storico(monkeypatch: pytest.MonkeyPatch, quanti: int = 6) -> list[Any]:
    """Storico finto per `MessageRepository.list_history`. Ritorna la lista, che i
    test possono allungare per simulare messaggi nuovi arrivati dopo."""
    base = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    messaggi: list[Any] = [
        SimpleNamespace(
            role="user" if i % 2 == 0 else "assistant",
            content=f"messaggio {i}",
            created_at=base + timedelta(minutes=i),
        )
        for i in range(quanti)
    ]

    class FakeMessageRepo:
        def __init__(self, session: Any) -> None: ...

        async def list_history(self, conversation_id: Any, *, limit: int = 30) -> list[Any]:
            return messaggi[-limit:]

    monkeypatch.setattr(engine, "MessageRepository", FakeMessageRepo)
    return messaggi


def _aggiungi_messaggi(messaggi: list[Any], quanti: int) -> None:
    ultimo = messaggi[-1].created_at
    for i in range(quanti):
        messaggi.append(
            SimpleNamespace(
                role="user",
                content=f"nuovo {i}",
                created_at=ultimo + timedelta(minutes=i + 1),
            )
        )


async def test_senza_flag_il_riassunto_non_si_paga(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il default e' spento: chi non lo chiede non chiama il modello."""
    _patch_storico(monkeypatch)
    router = _FakeRouterRiassunto()

    client = await _scrivi_tag(
        monkeypatch,
        {"field": "tag", "value": "VIP", "ghl_sync": True, "ghl_note": True},
        session=_SessionConConv(_FakeConv()),
        router=router,
    )

    assert router.chiamate == 0
    assert "Riassunto" not in client.notes[0][1]


async def test_riassunto_finisce_in_coda_alla_nota(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_storico(monkeypatch)
    router = _FakeRouterRiassunto()

    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_summary": True,
        },
        session=_SessionConConv(_FakeConv()),
        router=router,
    )

    body = client.notes[0][1]
    assert router.chiamate == 1
    assert body.endswith("Riassunto della conversazione: Il lead chiede il prezzo.")
    # L'intestazione di diario resta sopra: e' la parte certa, e il troncamento
    # a GHL_NOTE_MAX_LEN taglia da destra.
    assert body.startswith("[Reloop AI] Tag")


async def test_la_cache_evita_di_ripagare_lo_stesso_riassunto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il motore e' stateless e ripassa dal nodo: senza cache si pagherebbe a ogni
    evento. Con la stessa chat, la seconda passata non chiama il modello."""
    _patch_storico(monkeypatch)
    router = _FakeRouterRiassunto()
    conv = _FakeConv()
    cfg = {
        "field": "tag",
        "value": "VIP",
        "ghl_sync": True,
        "ghl_note": True,
        "ghl_note_summary": True,
    }

    primo = await _scrivi_tag(monkeypatch, cfg, session=_SessionConConv(conv), router=router)
    secondo = await _scrivi_tag(monkeypatch, cfg, session=_SessionConConv(conv), router=router)

    assert router.chiamate == 1
    assert conv.meta["crm_summary"]["text"] == "Il lead chiede il prezzo."
    assert primo.notes[0][1] == secondo.notes[0][1]


async def test_la_cache_si_rinfresca_quando_la_chat_va_avanti(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messaggi = _patch_storico(monkeypatch)
    router = _FakeRouterRiassunto()
    conv = _FakeConv()
    cfg = {
        "field": "tag",
        "value": "VIP",
        "ghl_sync": True,
        "ghl_note": True,
        "ghl_note_summary": True,
    }

    await _scrivi_tag(monkeypatch, cfg, session=_SessionConConv(conv), router=router)
    _aggiungi_messaggi(messaggi, engine._SUMMARY_REFRESH_AFTER_MESSAGES)
    router.reply = "Il lead ha accettato la proposta."
    client = await _scrivi_tag(monkeypatch, cfg, session=_SessionConConv(conv), router=router)

    assert router.chiamate == 2
    assert "accettato la proposta" in client.notes[0][1]


async def test_testo_personalizzato_che_non_cita_il_riassunto_non_lo_paga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una chiamata al modello che nessuno stampa e' solo un costo."""
    _patch_storico(monkeypatch)
    router = _FakeRouterRiassunto()

    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_summary": True,
            "ghl_note_text": "Tag applicato a {{lead.first_name}}",
        },
        session=_SessionConConv(_FakeConv()),
        router=router,
    )

    assert router.chiamate == 0
    assert client.notes[0][1] == "Tag applicato a Mario"


async def test_testo_personalizzato_puo_posizionare_il_riassunto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_storico(monkeypatch)
    router = _FakeRouterRiassunto()

    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_summary": True,
            "ghl_note_text": "{{lead.first_name}}: {{conversation.summary}}",
        },
        session=_SessionConConv(_FakeConv()),
        router=router,
    )

    assert client.notes[0][1] == "Mario: Il lead chiede il prezzo."


async def test_modello_giu_la_nota_si_scrive_lo_stesso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-open: una nota senza riassunto e' un dettaglio, una nota mancante no."""
    _patch_storico(monkeypatch)
    router = _FakeRouterRiassunto(error=RuntimeError("429"))

    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_summary": True,
        },
        session=_SessionConConv(_FakeConv()),
        router=router,
    )

    body = client.notes[0][1]
    assert "Riassunto" not in body
    assert "VIP" in body


async def test_chat_troppo_corta_non_chiama_il_modello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_storico(monkeypatch, quanti=2)
    router = _FakeRouterRiassunto()

    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_summary": True,
        },
        session=_SessionConConv(_FakeConv()),
        router=router,
    )

    assert router.chiamate == 0
    assert "Riassunto" not in client.notes[0][1]


async def test_senza_router_la_nota_resta_senza_riassunto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Il runtime senza orchestratore (test, job spogli) non deve far saltare la nota."""
    _patch_storico(monkeypatch)

    client = await _scrivi_tag(
        monkeypatch,
        {
            "field": "tag",
            "value": "VIP",
            "ghl_sync": True,
            "ghl_note": True,
            "ghl_note_summary": True,
        },
        session=_SessionConConv(_FakeConv()),
    )

    assert "Riassunto" not in client.notes[0][1]
    assert "VIP" in client.notes[0][1]
