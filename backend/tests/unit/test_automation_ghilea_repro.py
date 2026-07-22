"""Riproduzione e2e (in-process) dell'automazione "Nuovo lead" del merchant Ghilea.

Riproduce il comportamento osservato in produzione il 2026-07-21 (conversazione
ba24e5b6-882c-4296-9a85-280d50fd2450): il trigger CRM parte, il template di primo
contatto viene inviato, e **poi il flusso muore**. Il lead risponde "Si ok" alle
09:43:26 e nessun nodo successivo viene mai eseguito.

Il grafo qui sotto e' quello reale, letto dal DB di produzione (automation_flows
34094b0f-e86d-41ef-b47b-5ce0548ae422), nodi ed edge inclusi.

Causa: `_walk` e' una BFS **in un unico passaggio** al momento del trigger. L'unica
primitiva di pausa e' il nodo `wait` (deferral di N minuti); non esiste un resume
guidato dall'inbound del lead. Il grafo Ghilea non ha nodi `wait`, quindi tutte le
condizioni vengono valutate a t=0, quando il lead non ha ancora scritto nulla.

Fake sender / templates / ai_deps: niente DB, niente LLM, niente rete.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from workers.automation.engine import RunContext, _walk

# --- il grafo reale di Ghilea ------------------------------------------------

TEMPLATE_ID = "ff1e3544-ba2c-48b0-aaf2-4cf604defdc2"

GHILEA_NODES = [
    (
        "n1",
        "trigger",
        "crm_opportunity_created",
        {
            "pipeline_id": "EM6JTHMOBLpEn7jfQ6mq",
            "stage_id": "f9868c43-b983-4787-a668-4e50192a606e",
        },
    ),
    (
        "n2",
        "action",
        "send",
        {
            "free_text": "Ciao, come posso aiutarti?",
            "template_id": TEMPLATE_ID,
            "window_policy": "require_template",
            "variable_mapping": {"1": "lead.first_name"},
        },
    ),
    (
        "n3",
        "condition",
        "ai_check",
        {
            "model": "",
            "prompt": (
                "Il lead accetta di fissare un appuntamento, risponde ok, va bene, "
                "certo, dammi disponibilita"
            ),
        },
    ),
    (
        "n4",
        "action",
        "ai_reply",
        {
            "objective": "Chiedi preferenze, se mattina o pomeriggio",
            "window_policy": "freeform_only",
            "allowed_actions": ["propose_slots"],
            "extra_instructions": "cordiale",
        },
    ),
    (
        "n13",
        "action",
        "send",
        {
            "free_text": "Come mai? Posso aiutarti in qualche modo?",
            "template_id": "",
            "window_policy": "freeform_only",
            "variable_mapping": {},
        },
    ),
    (
        "n5",
        "condition",
        "condition_group",
        {
            "operator": "or",
            "clauses": [
                {"type": "message_contains", "keywords": ["Mattina"]},
                {"type": "message_contains", "keywords": ["Pomeriggio"]},
            ],
        },
    ),
    (
        "n6",
        "action",
        "ai_reply",
        {
            "objective": "Proponi slot di mattina nei 7 giorni",
            "window_policy": "auto",
            "allowed_actions": ["propose_slots"],
        },
    ),
    (
        "n8",
        "action",
        "ai_reply",
        {
            "objective": "Proponi slot di pomeriggio nei 7 giorni successivi",
            "window_policy": "auto",
            "allowed_actions": ["propose_slots"],
        },
    ),
    (
        "n11",
        "condition",
        "ai_check",
        {
            "model": "",
            "prompt": "Il lead ha accettato uno degli slot proposti",
        },
    ),
    ("n16", "condition", "ai_check", {"model": "", "prompt": "proponi slot il pomeriggio"}),
    ("n14", "action", "human_handoff", {"reason": "Non troviamo slot compatibili"}),
    (
        "n12",
        "action",
        "ai_reply",
        {
            "objective": "Conferma appuntamento",
            "window_policy": "auto",
            "allowed_actions": ["book_slot", "move_pipeline"],
        },
    ),
    (
        "n17",
        "action",
        "ai_reply",
        {
            "objective": "Fissa appuntamento se da ok per uno degli slot proposti",
            "window_policy": "auto",
            "allowed_actions": ["book_slot", "move_pipeline"],
        },
    ),
    (
        "n18",
        "action",
        "send",
        {
            "free_text": "Ok, dimmi tu quali disponibilita hai e ti faccio sapere. Ok?",
            "template_id": "",
            "window_policy": "freeform_only",
            "variable_mapping": {},
        },
    ),
    (
        "n19",
        "action",
        "ai_reply",
        {
            "objective": "Passa a operatore per confermare lo slot",
            "window_policy": "auto",
            "allowed_actions": ["escalate_human"],
        },
    ),
]

GHILEA_EDGES = [
    ("n1", "n2", "default"),
    ("n2", "n3", "default"),
    ("n3", "n4", "true"),
    ("n3", "n13", "false"),
    ("n4", "n5", "default"),
    ("n5", "n6", "true"),
    ("n5", "n8", "false"),
    ("n6", "n11", "default"),
    ("n8", "n16", "default"),
    ("n11", "n12", "true"),
    ("n11", "n14", "false"),
    ("n16", "n17", "true"),
    ("n16", "n18", "false"),
    ("n18", "n19", "default"),
]


def _ghilea_automation() -> Any:
    return SimpleNamespace(
        nodes=[
            SimpleNamespace(node_key=k, kind=kind, type=t, config=c)
            for k, kind, t, c in GHILEA_NODES
        ],
        edges=[SimpleNamespace(source_key=s, target_key=t, branch=b) for s, t, b in GHILEA_EDGES],
    )


# --- fakes ------------------------------------------------------------------


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
    """Il template di primo contatto di Ghilea, approvato."""

    async def get(self, _id: Any) -> Any:
        return SimpleNamespace(
            name="reloop_first_contact_0e1fac1c_tiinxw",
            language="it",
            variables=["1"],
            status="approved",
            body="Ciao {{1}}, sono l'assistente di Corina Ghilea.",
        )


def _run_ctx(*, within_window: bool, last_message: str, ai_paused: bool = False) -> RunContext:
    return RunContext(
        phone="393208043592",
        wa_phone_number_id="1169119659625359",
        within_window=within_window,
        score=0,
        temperature="cold",
        name="Gianluca",
        last_message=last_message,
        lead_id=uuid4(),
        conversation_id=uuid4(),
        tenant_id=uuid4(),
        merchant_id=uuid4(),
        api_key="k",
        ai_paused=ai_paused,
    )


# --- il test che riproduce il bug di produzione ------------------------------


async def test_ghilea_flow_dies_after_first_template() -> None:
    """Riproduce la produzione del 21/07: parte solo n2, poi silenzio.

    A t=0 (trigger CRM) il lead non ha ancora scritto:
      - `within_window=False`  (nessun inbound → finestra 24h chiusa)
      - `last_message=""`      (nessun messaggio del lead)

    `ai_deps=None` fa fallire *closed* ogni `ai_check` (engine.py:824-826,
    "no_deps" → False), esattamente come un ai_check valutato su una
    conversazione in cui il lead non ha ancora parlato.
    """
    sender = _FakeSender()
    sent, deferrals = await _walk(
        _ghilea_automation(),
        _run_ctx(within_window=False, last_message=""),
        start_keys=["n2"],
        sender=sender,
        templates=_FakeTemplates(),
        ai_deps=None,
    )

    # n2 parte: require_template + template approvato → inviato anche a finestra chiusa.
    assert sender.templates == ["reloop_first_contact_0e1fac1c_tiinxw"]

    # n3 (ai_check) → False → ramo n13 (send freeform_only) → finestra chiusa → skip.
    assert sender.texts == [], "nessun testo libero puo' uscire a finestra chiusa"
    assert sent == 1, "un solo nodo ha prodotto un invio: n2"

    # IL PUNTO CHIAVE: nessun deferral → nessun job di resume viene mai schedulato.
    # Il grafo non ha nodi `wait`, quindi il walk finisce qui e non ripartira' mai,
    # qualunque cosa il lead risponda dopo.
    assert deferrals == [], (
        "il flusso Ghilea non schedula alcun resume: quando il lead rispondera' "
        "'Si ok' non ci sara' nessun run a raccoglierlo"
    )


async def test_ghilea_both_branches_of_n3_are_dead_at_trigger_time() -> None:
    """A t=0 il verdetto dell'ai_check n3 e' irrilevante: entrambi i rami muoiono.

    Asserzione piu' forte della precedente perche' non dipende da cosa risponde
    l'LLM: al momento del trigger la finestra 24h e' chiusa (il lead non ha mai
    scritto), e
      - ramo true  → n4  = ai_reply  window_policy="freeform_only" → skip
      - ramo false → n13 = send      window_policy="freeform_only" → skip
    Solo n2 ("require_template") puo' uscire. Il flusso e' strutturalmente
    incapace di proseguire nello stesso passaggio del trigger.
    """
    for verdict_branch in ("true", "false"):
        sender = _FakeSender()
        start = "n4" if verdict_branch == "true" else "n13"
        sent, deferrals = await _walk(
            _ghilea_automation(),
            _run_ctx(within_window=False, last_message=""),
            start_keys=[start],
            sender=sender,
            templates=_FakeTemplates(),
            ai_deps=None,
        )
        assert sender.texts == [], f"ramo {verdict_branch}: nulla puo' uscire"
        assert sent == 0, f"ramo {verdict_branch}: nessun invio a finestra chiusa"
        assert deferrals == []


async def test_ghilea_walk_is_single_pass_no_inbound_resume() -> None:
    """Anche col lead che ha risposto, il walk resta un unico passaggio.

    Se si simula il turno "ideale" (lead ha scritto, finestra aperta, ai_check
    veri), il walk percorre l'intero albero conversazionale in un colpo solo:
    n4 (chiedi mattina/pomeriggio), n5, n6/n8 (proponi slot), n11/n16, n12/n17...
    Tutti valutati sullo STESSO ultimo messaggio, senza mai attendere la replica
    del lead fra un nodo e l'altro. Nessun deferral viene prodotto.
    """
    sender = _FakeSender()
    _, deferrals = await _walk(
        _ghilea_automation(),
        _run_ctx(within_window=True, last_message="Si ok"),
        start_keys=["n2"],
        sender=sender,
        templates=_FakeTemplates(),
        ai_deps=None,
    )
    assert deferrals == [], "nessun nodo `wait` nel grafo → nessuna pausa possibile"


async def test_ghilea_n5_routes_pomeriggio_to_the_morning_branch() -> None:
    """Bug di configurazione: n5 e' un OR fra 'Mattina' e 'Pomeriggio'.

    Chi risponde "Pomeriggio" soddisfa l'OR → ramo `true` → n6 = slot di MATTINA.
    Il ramo `false` (n8, pomeriggio) e' raggiungibile solo da chi NON nomina ne'
    mattina ne' pomeriggio. Le due branche sono di fatto invertite/inutili.
    """
    from ai_core.automations import evaluate_condition

    cfg = dict(GHILEA_NODES[5][3])  # n5
    assert GHILEA_NODES[5][0] == "n5"

    def _passes(msg: str) -> bool:
        ctx = _run_ctx(within_window=True, last_message=msg).as_condition_context()
        return evaluate_condition("condition_group", cfg, ctx)

    # "Pomeriggio" fa passare l'OR → true → n6 → slot di MATTINA. Sbagliato.
    assert _passes("Pomeriggio") is True, (
        "chi chiede il pomeriggio finisce sul ramo true = slot di mattina"
    )
    assert _passes("Mattina") is True
    # Chi non nomina nessuna delle due finisce sul ramo pomeriggio.
    assert _passes("indifferente") is False
