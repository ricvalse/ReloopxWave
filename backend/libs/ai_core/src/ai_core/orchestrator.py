"""ConversationOrchestrator — the single entry point for every conversation turn.

Flow (section 6.1):
  build context -> select model -> call LLM -> parse structured output -> return actions.

The response is constrained by a Pydantic schema so downstream workers can
dispatch book_slot / move_pipeline / update_score / escalate_human without
parsing free-form text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from ai_core.llm import ChatMessage, ImagePart, LLMClient
from ai_core.rag import RetrievedChunk
from ai_core.router import ModelRouter, RoutingRequest
from shared import get_logger

logger = get_logger(__name__)


ActionKind = Literal[
    # Read-only "tools": the orchestrator executes these mid-turn and feeds the
    # real result back to the model before it composes the reply (Amalia-style
    # grounding). They never reach the post-turn action dispatcher.
    "check_availability",
    "lookup_appointment",
    # Side-effecting actions: dispatched after the reply is sent.
    "propose_slots",
    "book_slot",
    "reschedule_slot",
    "cancel_slot",
    "move_pipeline",
    "update_score",
    "handoff_human",
    # Nome storico di `handoff_human`, accettato SOLO in lettura (ADR 0026).
    # Serve a due sorgenti che non possiamo riscrivere a comando: le allowlist
    # `allowed_actions` già salvate dai merchant sulla lavagnetta e in
    # `conversation.playbook.actions.enabled`, e un modello che continui a
    # emettere il vecchio nome finché la cache del prompt non gira. Un valore
    # fuori dal Literal farebbe fallire `model_validate_json`, e il ramo di
    # fallback rispedisce `reply_text = raw`: il cliente riceverebbe il JSON
    # grezzo su WhatsApp. `_normalize_action_kind` lo traduce subito dopo il
    # parse, così il resto del codice vede un nome solo.
    "escalate_human",
    "none",
]

# Nome legacy → nome corrente. Applicato in ingresso su tutto ciò che arriva
# dall'esterno: risposte del modello e allowlist lette dal DB.
_LEGACY_ACTION_KINDS: dict[str, str] = {"escalate_human": "handoff_human"}


def normalize_action_kind(kind: str) -> str:
    """Traduce un action kind legacy nel nome corrente (ADR 0026)."""
    return _LEGACY_ACTION_KINDS.get(kind, kind)


# The subset of actions that are read-only tool calls grounded mid-turn. Kept in
# one place so the orchestrator loop and the conversation service agree on which
# actions feed the model vs. which dispatch as side effects.
READ_TOOL_KINDS: frozenset[str] = frozenset({"check_availability", "lookup_appointment"})


@dataclass(slots=True, frozen=True)
class ToolResult:
    """Outcome of one read-tool call, fed back to the model as an observation."""

    kind: str
    ok: bool
    # Italian, model-facing summary of what the tool found (e.g. the free slots,
    # the upcoming appointment). Reinjected verbatim into the conversation.
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


class ToolExecutor(Protocol):
    """Executes a read-only tool call for the orchestrator loop.

    Implemented by the conversation service (it owns GHL access). The orchestrator
    stays IO-free: it only decides *when* to call a tool and how to reincorporate
    the result.
    """

    async def execute_read(
        self, action: OrchestratorAction, ctx: ConversationContext
    ) -> ToolResult: ...


class OrchestratorAction(BaseModel):
    kind: ActionKind
    payload: dict[str, Any] = Field(default_factory=dict)


class OrchestratorResponse(BaseModel):
    reply_text: str
    actions: list[OrchestratorAction] = Field(default_factory=list)
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


@dataclass(slots=True)
class ConversationContext:
    merchant_id: UUID
    tenant_id: UUID
    lead_id: UUID | None
    lead_score: int
    hot_threshold: int
    system_prompt: str
    history: list[ChatMessage] = field(default_factory=list)
    kb_chunks: list[RetrievedChunk] = field(default_factory=list)
    variant_id: str | None = None
    # Merchant-configured lead score (0-100) at/above which the lead should be
    # advanced in the pipeline. Surfaced to the model as decision context for
    # `move_pipeline` (config key `pipeline.advance_threshold`).
    advance_threshold: int = 60
    # --- Use-case playbook caps (ADR 0018). Defaults preserve today's behavior. ---
    # Action allowlist; None = all actions allowed. Restricts both the schema hint
    # shown to the model and the actions accepted from it.
    allowed_actions: set[str] | None = None
    # When False the lead-qualification context (score/threshold) is dropped from
    # the prompt — for bots that don't qualify leads.
    scoring_enabled: bool = True
    # Authoritative behavioral rules injected high-salience (playbook directives).
    directives: tuple[str, ...] = ()
    # Keyword vocabulary that forces the escalation model route. None = code
    # default (CRITICAL_KEYWORDS).
    critical_keywords: tuple[str, ...] | None = None
    # Vision attachment for THIS turn (customer just sent a photo). When set, the
    # image rides the current user message and the "you can't see media" note is
    # swapped for a "you CAN see the attached image" directive. None = text turn.
    current_image: ImagePart | None = None
    # Istruzioni di handoff del merchant (ADR 0026). None = i tre criteri
    # storici, cioè il prompt di prima.
    handoff: HandoffPrompt | None = None


class ConversationOrchestrator:
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def run(
        self,
        ctx: ConversationContext,
        user_message: str,
        *,
        tool_executor: ToolExecutor | None = None,
        max_iterations: int = 1,
    ) -> OrchestratorResponse:
        """Run one conversation turn.

        With `max_iterations == 1` (or no `tool_executor`) this is the classic
        single-shot structured-JSON turn. With a tool executor and
        `max_iterations > 1` it becomes an Amalia-style tool-use loop: when the
        model emits a read-only tool call (`check_availability` /
        `lookup_appointment`), the orchestrator executes it, reinjects the real
        result as an observation, and lets the model finish — so it never
        promises an unavailable slot. Token/latency totals accumulate across the
        loop; read-tool actions are stripped from the returned actions (they're
        handled here, not by the post-turn dispatcher).
        """
        messages = self._build_messages(ctx, user_message)
        context_tokens = sum(len(m.content) for m in messages) // 4  # rough estimate

        req = RoutingRequest(
            merchant_id=ctx.merchant_id,
            tenant_id=ctx.tenant_id,
            context_tokens=context_tokens,
            turn_count=len(ctx.history),
            lead_score=ctx.lead_score,
            hot_threshold=ctx.hot_threshold,
            escalate_keywords_matched=_has_critical_objection(user_message, ctx.critical_keywords),
            variant_id=ctx.variant_id,
        )
        client: LLMClient = await self._router.select(req)

        total_in = total_out = total_latency = 0
        model_used = client.model
        iterations = max(1, max_iterations)
        parsed = _StructuredResponse(reply_text="", actions=[])

        for iteration in range(iterations):
            result = await self._complete(client, messages)
            total_in += result.tokens_in
            total_out += result.tokens_out
            total_latency += result.latency_ms
            model_used = result.model
            parsed = _parse_structured(result.content)

            read_actions = [a for a in parsed.actions if a.kind in READ_TOOL_KINDS]
            is_last = iteration == iterations - 1
            if not read_actions or tool_executor is None or is_last:
                break

            observations = await self._run_read_tools(read_actions, ctx, tool_executor)
            messages.append(ChatMessage(role="assistant", content=result.content))
            messages.append(ChatMessage(role="user", content=observations))

        # Read-only tool calls were handled in the loop — never forward them to
        # the post-turn action dispatcher.
        final_actions = [a for a in parsed.actions if a.kind not in READ_TOOL_KINDS]
        # Playbook action allowlist — drop any side effect the playbook forbids
        # (belt-and-suspenders: the schema hint already omits them). None = all.
        if ctx.allowed_actions is not None:
            final_actions = [a for a in final_actions if a.kind in ctx.allowed_actions]
        return OrchestratorResponse(
            reply_text=parsed.reply_text,
            actions=final_actions,
            model=model_used,
            tokens_in=total_in,
            tokens_out=total_out,
            latency_ms=total_latency,
        )

    async def _complete(self, client: LLMClient, messages: list[ChatMessage]) -> Any:
        """One LLM call with the JSON response format + Anthropic fallback.

        The fallback now also receives the JSON response_format hint so structured
        actions survive a failover (the system prompt already mandates the schema).
        """
        try:
            return await client.complete(
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.warning("orchestrator.llm_failed", error=str(e), model=client.model)
            fallback = await self._router.fallback()
            if fallback is None:
                raise
            return await fallback.complete(
                messages=messages,
                response_format={"type": "json_object"},
            )

    @staticmethod
    async def _run_read_tools(
        read_actions: list[OrchestratorAction],
        ctx: ConversationContext,
        tool_executor: ToolExecutor,
    ) -> str:
        """Execute each read tool and format the results as a model observation."""
        lines: list[str] = []
        for a in read_actions:
            try:
                tr = await tool_executor.execute_read(a, ctx)
                lines.append(f"- {a.kind}: {tr.summary}")
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("orchestrator.tool_failed", kind=a.kind, error=str(e))
                lines.append(f"- {a.kind}: strumento non disponibile al momento.")
        return (
            "RISULTATO STRUMENTI (uso interno, non incollarlo grezzo al cliente):\n"
            + "\n".join(lines)
            + "\n\nUsa SOLO questi dati reali per rispondere in modo veritiero: non "
            "promettere nulla che questi risultati non confermino. Ora scrivi la "
            "risposta finale al cliente rispettando lo schema JSON."
        )

    def _build_messages(self, ctx: ConversationContext, user_message: str) -> list[ChatMessage]:
        system_parts = [
            ctx.system_prompt,
            render_schema_hint(
                ctx.allowed_actions,
                viewable_media=ctx.current_image is not None,
                handoff=ctx.handoff,
            ),
        ]
        # Qualification context (internal — never repeat the number to the lead):
        # gives the model the current score + the merchant's configured advance
        # threshold so `move_pipeline` fires in line with the merchant's setting.
        # Dropped entirely for bots that don't qualify leads (scoring off).
        if ctx.scoring_enabled:
            system_parts.append(
                "Stato qualificazione del lead (uso interno, non citarlo al cliente): "
                f"punteggio attuale {ctx.lead_score}/100; soglia di avanzamento pipeline "
                f"configurata dal merchant {ctx.advance_threshold}. Emetti `move_pipeline` "
                "quando il lead è qualificato e il punteggio è vicino o superiore alla soglia."
            )
        if ctx.kb_chunks:
            kb_snippet = "\n---\n".join(
                f"[{i + 1}] {c.content}" for i, c in enumerate(ctx.kb_chunks)
            )
            system_parts.append(f"Knowledge base context:\n{kb_snippet}")
        # Playbook directives — authoritative behavioral rules. Injected LAST so
        # they win on salience over the persona/schema/KB context (ADR 0018).
        if ctx.directives:
            system_parts.append(_directives_block(ctx.directives))
        messages = [ChatMessage(role="system", content="\n\n".join(system_parts))]
        messages.extend(ctx.history)
        messages.append(ChatMessage(role="user", content=user_message, image=ctx.current_image))
        return messages

    async def run_proactive(
        self,
        ctx: ConversationContext,
        *,
        objective: str,
        extra_instructions: str = "",
        allowed_actions: set[str] | None = None,
        force_model: str | None = None,
    ) -> OrchestratorResponse:
        """Generate a single bot-initiated message for an automation node.

        Unlike `run`, there is no inbound user turn: the model is instructed to
        start/continue the conversation toward `objective`, using `ctx.history`
        for context. `allowed_actions`, when given, filters the parsed actions so
        an automation can restrict which side effects the AI may trigger;
        `force_model` pins a specific model (the node's `model_override`).
        """
        messages = self._build_proactive_messages(ctx, objective, extra_instructions)
        context_tokens = sum(len(m.content) for m in messages) // 4  # rough estimate

        req = RoutingRequest(
            merchant_id=ctx.merchant_id,
            tenant_id=ctx.tenant_id,
            context_tokens=context_tokens,
            turn_count=len(ctx.history),
            lead_score=ctx.lead_score,
            hot_threshold=ctx.hot_threshold,
            escalate_keywords_matched=False,
            variant_id=ctx.variant_id,
            force_model=force_model,
        )
        client: LLMClient = await self._router.select(req)

        result = await self._complete(client, messages)

        parsed = _parse_structured(result.content)
        # Read-only tool calls are meaningless for a proactive (no-inbound) nudge
        # — drop them along with any action the automation node OR the playbook
        # allowlist forbids (the effective set is the intersection of the two).
        actions = [a for a in parsed.actions if a.kind not in READ_TOOL_KINDS]
        effective_allowed = _combine_allowlists(allowed_actions, ctx.allowed_actions)
        if effective_allowed is not None:
            actions = [a for a in actions if a.kind in effective_allowed]
        return OrchestratorResponse(
            reply_text=parsed.reply_text,
            actions=actions,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
        )

    def _build_proactive_messages(
        self, ctx: ConversationContext, objective: str, extra_instructions: str
    ) -> list[ChatMessage]:
        system_parts = [
            ctx.system_prompt,
            render_schema_hint(ctx.allowed_actions, handoff=ctx.handoff),
        ]
        if ctx.scoring_enabled:
            system_parts.append(
                "Stato qualificazione del lead (uso interno, non citarlo al cliente): "
                f"punteggio attuale {ctx.lead_score}/100; soglia di avanzamento pipeline "
                f"configurata dal merchant {ctx.advance_threshold}."
            )
        if ctx.kb_chunks:
            kb_snippet = "\n---\n".join(
                f"[{i + 1}] {c.content}" for i, c in enumerate(ctx.kb_chunks)
            )
            system_parts.append(f"Knowledge base context:\n{kb_snippet}")
        if ctx.directives:
            system_parts.append(_directives_block(ctx.directives))
        directive = (
            "Sei tu ad avviare/riprendere la conversazione: NON è arrivato un nuovo "
            f"messaggio dal cliente. Obiettivo di questo messaggio: {objective.strip()}."
        )
        if extra_instructions.strip():
            directive += f"\nIstruzioni aggiuntive: {extra_instructions.strip()}"
        directive += (
            "\nGenera un unico messaggio WhatsApp proattivo, coerente con la storia qui "
            "sopra e con il tono dell'assistente, e rispetta lo schema JSON."
        )
        messages = [ChatMessage(role="system", content="\n\n".join(system_parts))]
        messages.extend(ctx.history)
        # No inbound user turn — the directive is delivered as the final user-role
        # message for maximum provider compatibility (avoids a trailing system msg).
        messages.append(ChatMessage(role="user", content=directive))
        return messages


class _StructuredResponse(BaseModel):
    reply_text: str
    actions: list[OrchestratorAction] = Field(default_factory=list)


# The response-schema hint is assembled from a SINGLE source (header + per-action
# snippets + trailing notes) so it can be RENDERED FROM AN ALLOWLIST: a use-case
# playbook that permits only a subset of actions gets a prompt that never even
# mentions the others (ADR 0018). `render_schema_hint(None)` reproduces the full
# hint byte-for-byte (verified by a golden test) — the default sales path.

# Canonical action order (matches the enum order the model was tuned on).
_ACTION_ORDER: tuple[str, ...] = (
    "check_availability",
    "lookup_appointment",
    "propose_slots",
    "book_slot",
    "reschedule_slot",
    "cancel_slot",
    "move_pipeline",
    "update_score",
    "handoff_human",
    "none",
)
# Read-only tool actions — the tool-use paragraph is only shown when at least one
# of these is allowed.
_READ_TOOL_ACTIONS: frozenset[str] = frozenset({"check_availability", "lookup_appointment"})
# Booking-family actions — the "niente false conferme" note is only shown when at
# least one of these is allowed.
_BOOKING_ACTIONS: frozenset[str] = frozenset(
    {"propose_slots", "book_slot", "reschedule_slot", "cancel_slot"}
)

_TOOL_USE_PARAGRAPH = (
    "STRUMENTI DI LETTURA (check_availability, lookup_appointment): se ti servono "
    "dati reali per rispondere con verità (è libero quello slot? che appuntamento "
    "ha il cliente?), EMETTI lo strumento e metti in `reply_text` solo una frase di "
    "attesa ('controllo subito', 'un attimo che verifico'). Riceverai il RISULTATO "
    "STRUMENTI e poi scriverai la risposta definitiva con i dati veri. NON inventare "
    "disponibilità o dettagli che non hai verificato.\n"
)

_ACTION_INTRO = "Emetti le azioni SOLO quando i criteri sono soddisfatti, riempiendo il payload:\n"

# Per-action snippet, keyed by kind. Concatenated (in _ACTION_ORDER) after the
# intro. Each already carries its trailing newline.
_ACTION_SNIPPETS: dict[str, str] = {
    "check_availability": (
        '- "check_availability": quando l\'utente chiede se un orario/giorno è libero o '
        "quali disponibilità ci sono. payload: {\n"
        '    "preferred_start_iso": "<ISO8601 se ha indicato un orario preciso, opzionale>",\n'
        '    "lookahead_days": <numero giorni da guardare, opzionale>\n'
        "  }\n"
    ),
    "lookup_appointment": (
        '- "lookup_appointment": quando l\'utente fa riferimento a "il mio appuntamento" '
        "(per spostarlo, annullarlo o chiederne i dettagli) e ti serve sapere qual è. "
        "payload: {}\n"
    ),
    "propose_slots": (
        '- "propose_slots": quando l\'utente vuole prenotare ma NON ha ancora indicato '
        'un orario preciso (chiede disponibilità, "quando siete liberi?"). Mostra gli '
        "slot liberi così l'utente ne sceglie uno. payload: {} (reply_text può anticipare "
        '"ti mostro le disponibilità").\n'
    ),
    "book_slot": (
        '- "book_slot": quando l\'utente vuole prenotare/fissare un appuntamento o '
        "accetta uno slot proposto. payload: {\n"
        '    "preferred_start_iso": "<ISO8601 COMPLETO di anno, formato AAAA-MM-GGThh:mm:ss, '
        'calcolato rispetto alla «Data e ora attuali» indicata nel prompt — mai un anno passato>",\n'
        '    "service_id": "<UUID del servizio scelto dall\'elenco \\"Servizi '
        "prenotabili\\\" del prompt — OBBLIGATORIO quando quell'elenco è presente. "
        "Se l'utente non ha ancora scelto un servizio NON prenotare: chiedigli "
        'quale servizio desidera>",\n'
        '    "contact_fields": {"name": "<se noto>", "email": "<se noto>"}\n'
        "  }\n"
    ),
    "reschedule_slot": (
        '- "reschedule_slot": quando l\'utente vuole SPOSTARE/cambiare un appuntamento '
        "già fissato. payload: {\n"
        '    "preferred_start_iso": "<ISO8601 della nuova data/ora, se indicata>"\n'
        "  }\n"
    ),
    "cancel_slot": (
        '- "cancel_slot": quando l\'utente vuole ANNULLARE/disdire un appuntamento già '
        "fissato. payload: {}\n"
    ),
    "move_pipeline": (
        '- "move_pipeline": quando il lead è chiaramente qualificato e pronto ad '
        "avanzare (intenzione forte, budget/tempistiche confermati). payload: {\n"
        '    "stage": "<nome stage target, opzionale>"\n'
        "  }\n"
    ),
    "update_score": (
        '- "update_score": ad OGNI turno in cui il messaggio rivela qualcosa di '
        "rilevante sul lead. payload: {\n"
        '    "signals": { ... usa SOLO queste chiavi booleane, true se vere in QUESTO '
        "messaggio ... }\n"
        "  }\n"
        "  chiavi valide per signals: has_name, has_email, has_budget, has_timeline, "
        "asked_for_booking, objection_price, objection_trust, objection_competitor, "
        "dropped_off, profanity.\n"
    ),
    "none": '- "none": negli altri casi.\n',
}

# --------------------------------------------------------------------------- #
# Handoff — istruzioni configurabili (ADR 0026)
# --------------------------------------------------------------------------- #
# I tre criteri storici. Restano il default di sistema: un merchant che non
# configura nulla vede esattamente il prompt di prima.
_HANDOFF_DEFAULT_CRITERIA: tuple[str, ...] = (
    "l'utente è arrabbiato",
    "minaccia reclami/azioni legali",
    "chiede esplicitamente una persona",
)

_HANDOFF_PAYLOAD = (
    " payload: {\n"
    '    "reason": "<motivo breve, es. cliente_arrabbiato/richiesta_umano>",\n'
    '    "customer_message_summary": "<1-2 frasi che riassumono cosa serve al '
    "cliente, per l'operatore che prende in carico>\"\n"
    "  }\n"
)

# Iniettato quando l'handoff è spento. Senza questo il modello non sa che non
# esiste nessun operatore: continua a scrivere «ti passo un collega», la frase
# parte davvero verso il cliente e l'azione viene scartata a valle — il cliente
# resta ad aspettare una persona che non arriverà mai.
_HANDOFF_DISABLED_NOTE = (
    "PASSAGGIO A OPERATORE NON DISPONIBILE: per questa attività non c'è nessun "
    "operatore umano a cui passare la conversazione. Non promettere MAI al "
    "cliente che lo metterai in contatto con una persona, che «passi la cosa a "
    "un collega» o che «qualcuno lo richiamerà». Gestisci tu la richiesta fino "
    "in fondo con i mezzi che hai, oppure spiega con onestà cosa puoi e non puoi "
    "fare.\n"
)


@dataclass(frozen=True, slots=True)
class HandoffPrompt:
    """Istruzioni di handoff risolte dalla cascata, pronte per il prompt.

    I default riproducono il comportamento storico, così ogni call-site che non
    le passa continua a generare il prompt identico a prima.
    """

    enabled: bool = True
    # "extend" = i criteri del merchant si sommano ai default; "replace" = li
    # sostituiscono.
    mode: str = "extend"
    criteria: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()

    def resolved_criteria(self) -> tuple[str, ...]:
        if self.mode == "replace":
            return self.criteria
        return _HANDOFF_DEFAULT_CRITERIA + self.criteria


def _join_criteria(criteria: tuple[str, ...]) -> str:
    """«a, b, o c» — la prosa che il prompt storico usava per i tre default."""
    if len(criteria) == 1:
        return criteria[0]
    return ", ".join(criteria[:-1]) + ", o " + criteria[-1]


def _handoff_snippet(cfg: HandoffPrompt) -> str:
    """Rende lo snippet dell'azione `handoff_human` per la config data.

    Con i soli default la resa è in prosa, byte-identica al prompt storico.
    Appena il merchant aggiunge criteri si passa a un elenco puntato: una prosa
    con otto condizioni in fila è illeggibile anche per il modello.
    """
    criteria = cfg.resolved_criteria()
    if not criteria:
        # `replace` senza criteri: non resta nessuna condizione. L'azione viene
        # comunque tolta dall'enum a monte — questo è solo il ramo difensivo.
        return ""
    if criteria == _HANDOFF_DEFAULT_CRITERIA:
        return f'- "handoff_human": quando {_join_criteria(criteria)}.{_HANDOFF_PAYLOAD}'
    bullets = "".join(f"    * {c}\n" for c in criteria)
    return (
        '- "handoff_human": quando si verifica UNO di questi casi:\n'
        f"{bullets}"
        f"  {_HANDOFF_PAYLOAD.lstrip()}"
    )


def _handoff_exclusions_block(exclusions: tuple[str, ...]) -> str:
    """Blocco negativo: i casi in cui NON si passa a un operatore.

    È la generalizzazione della nota sui media ("un media da solo non è un
    motivo di handoff"), che era l'unica eccezione e viveva cablata nel codice.
    """
    if not exclusions:
        return ""
    bullets = "".join(f"- {e}\n" for e in exclusions)
    return (
        "QUANDO **NON** PASSARE A UN OPERATORE — questi casi NON sono handoff, "
        "gestiscili tu normalmente:\n"
        f"{bullets}"
    )


_MULTI_ACTION_NOTE = "Puoi emettere più azioni nello stesso turno (es. update_score + book_slot).\n"

_MEDIA_NOTE = (
    "MEDIA: le righe tipo [Il cliente ha inviato un'immagine / un messaggio "
    "vocale / una posizione] indicano contenuti che NON puoi vedere né "
    "ascoltare. Non fingere di averli visti e non inventarne il contenuto: di' "
    "con naturalezza che da qui non riesci a visualizzarli e chiedi di scrivere "
    "a parole l'informazione che serve. Più media di fila "
    "nello stesso messaggio contano come un unico contenuto: una sola risposta "
    "per il gruppo, non una per ciascun media.\n"
)

# Coda della nota MEDIA aggiunta solo quando `handoff_human` è davvero fra le
# azioni disponibili. Prima era cucita dentro la nota: con un playbook che non
# permette l'handoff il prompt continuava a nominare un'azione assente
# dall'enum, cioè istruiva il modello su qualcosa che non poteva emettere.
_MEDIA_NO_HANDOFF_TAIL = (
    "Un media da solo NON è un motivo per handoff_human — vale solo per i criteri elencati sopra.\n"
)

# Replaces _MEDIA_NOTE when the customer's current turn carries a viewable image
# (the bytes ARE attached to this request). Without this swap the model is told
# it can't see media and refuses the very photo it's looking at — the class of
# regression where a stale prompt hint overrides real context.
_MEDIA_VIEWABLE_NOTE = (
    "MEDIA: il cliente ha allegato un'immagine a QUESTO messaggio e tu la stai "
    "vedendo. Rispondi nel merito di ciò che mostra (descrivila, rispondi alla "
    "domanda, riconosci il prodotto/documento) senza dire che non puoi vederla. "
    "Se l'immagine è illeggibile o non pertinente, dillo con naturalezza e chiedi "
    "un chiarimento.\n"
)

# Come `_MEDIA_NO_HANDOFF_TAIL`, per il ramo con immagine visibile.
_MEDIA_VIEWABLE_NO_HANDOFF_TAIL = "Un'immagine da sola NON è un motivo per handoff_human.\n"

_NO_FALSE_CONFIRM_NOTE = (
    "IMPORTANTE — niente false conferme: per book_slot / reschedule_slot / "
    "cancel_slot / propose_slots la conferma reale (con l'esito vero: prenotato, "
    "slot occupato + alternative, spostato...) viene inviata dal sistema DOPO il "
    "tuo messaggio. Quindi in `reply_text` NON dire che è già fatto ('ho prenotato', "
    "'appuntamento spostato'): scrivi una frase di passaggio ('procedo subito e ti "
    "confermo', 'un attimo che verifico'). Se vuoi essere certo della disponibilità "
    "prima di proporre un orario, usa check_availability."
)


def _schema_header(kinds: list[str]) -> str:
    enum = "|".join(kinds)
    return (
        "Rispondi SEMPRE con un JSON valido che rispetta esattamente questo schema:\n"
        "{\n"
        '  "reply_text": "<testo da inviare all\'utente>",\n'
        '  "actions": [\n'
        f'    {{"kind": "{enum}", "payload": {{}}}}\n'
        "  ]\n"
        "}\n"
        "`reply_text` non deve mai essere vuoto. `actions` può essere lista vuota.\n"
    )


def render_schema_hint(
    allowed: set[str] | None,
    *,
    viewable_media: bool = False,
    handoff: HandoffPrompt | None = None,
) -> str:
    """Render the response-schema hint, restricted to an action allowlist.

    `allowed=None` reproduces the full hint verbatim (the default sales path;
    a golden test pins byte-identity). When an allowlist is given, actions not
    in it are omitted from the enum, the per-action list, the tool-use paragraph
    and the booking note — so the model is never told about actions the playbook
    forbids. `none` is always available.

    `handoff` porta i criteri configurati dal merchant (ADR 0026); `None` = i
    tre criteri storici, resa byte-identica al prompt di prima. Con
    `handoff.enabled=False` l'azione sparisce dall'enum e al suo posto compare
    la nota che vieta di promettere un operatore.

    `viewable_media=True` swaps the "you can't see media" note for the vision
    directive — set only when a real image is attached to the current turn, so
    the default (text) output stays byte-identical.

    NOTA (correzione ADR 0026): la versione precedente di questa docstring
    dichiarava che `handoff_human` restava sempre disponibile "come valvola di
    sicurezza" anche fuori dall'allowlist. Il codice non lo faceva e nessun test
    lo copriva: un playbook che restringeva le azioni spegneva l'handoff in
    silenzio. Ora la valvola è reale — vedi sotto.
    """
    cfg = handoff or HandoffPrompt()
    handoff_available = cfg.enabled and bool(cfg.resolved_criteria())

    if allowed is None:
        kinds = list(_ACTION_ORDER)
    else:
        allow = set(allowed) | {"none"}
        # La valvola di sicurezza, ora davvero applicata: un allowlist non vuoto
        # non può togliere l'handoff. Restringere le azioni serve a evitare che
        # il bot prenoti o muova la pipeline dove non deve — non a intrappolare
        # un cliente arrabbiato in una conversazione da cui nessuno può tirarlo
        # fuori. Per spegnere l'handoff c'è `handoff.enabled`, che è esplicito.
        if allow != {"none"}:
            allow.add("handoff_human")
        kinds = [k for k in _ACTION_ORDER if k in allow]
        if not kinds:
            kinds = ["none"]

    # Handoff spento (o `replace` senza criteri): via dall'enum, così il modello
    # non può nemmeno emetterlo.
    if not handoff_available:
        kinds = [k for k in kinds if k != "handoff_human"] or ["none"]

    parts = [_schema_header(kinds), "\n"]
    if any(k in _READ_TOOL_ACTIONS for k in kinds):
        parts.append(_TOOL_USE_PARAGRAPH)
        parts.append("\n")
    parts.append(_ACTION_INTRO)
    parts.append("\n")
    for k in kinds:
        parts.append(_handoff_snippet(cfg) if k == "handoff_human" else _ACTION_SNIPPETS[k])
    # Multi-action note only makes sense with 2+ side-effect actions. Keep the
    # exact original wording when its example actions are allowed (byte-identity
    # for the full set); use a generic note for other multi-action subsets.
    real_actions = [k for k in kinds if k != "none"]
    if len(real_actions) >= 2:
        if "update_score" in kinds and "book_slot" in kinds:
            parts.append(_MULTI_ACTION_NOTE)
        else:
            parts.append("Puoi emettere più azioni nello stesso turno.\n")
    # Eccezioni del merchant: subito sotto la definizione dell'azione, dove il
    # modello le legge insieme ai criteri che devono limitare.
    if "handoff_human" in kinds:
        exclusions = _handoff_exclusions_block(cfg.exclusions)
        if exclusions:
            parts.append("\n")
            parts.append(exclusions)
    parts.append("\n")
    parts.append(_MEDIA_VIEWABLE_NOTE if viewable_media else _MEDIA_NOTE)
    if "handoff_human" in kinds:
        parts.append(_MEDIA_VIEWABLE_NO_HANDOFF_TAIL if viewable_media else _MEDIA_NO_HANDOFF_TAIL)
    if not cfg.enabled:
        parts.append("\n")
        parts.append(_HANDOFF_DISABLED_NOTE)
    if any(k in _BOOKING_ACTIONS for k in kinds):
        parts.append("\n")
        parts.append(_NO_FALSE_CONFIRM_NOTE)
    return "".join(parts)


# Full hint (all actions) — the default sales path. Kept as a module constant so
# existing references resolve; equals `render_schema_hint(None)` byte-for-byte.
_RESPONSE_SCHEMA_HINT = render_schema_hint(None)

CRITICAL_KEYWORDS = (
    "reclamo",
    "avvocato",
    "truffa",
    "rimborso immediato",
    "denuncia",
    "concorrenza",
)


def _combine_allowlists(a: set[str] | None, b: set[str] | None) -> set[str] | None:
    """Intersect two action allowlists, treating None as 'all allowed'."""
    if a is None:
        return set(b) if b is not None else None
    if b is None:
        return set(a)
    return set(a) & set(b)


def _has_critical_objection(text: str, keywords: tuple[str, ...] | None = None) -> bool:
    kws = keywords if keywords is not None else CRITICAL_KEYWORDS
    t = text.lower()
    return any(kw in t for kw in kws)


def _directives_block(directives: tuple[str, ...]) -> str:
    """Render the playbook directives as an authoritative, numbered prompt block."""
    lines = [
        "REGOLE DELLA CONVERSAZIONE (istruzione prioritaria e vincolante — hanno "
        "la precedenza su ogni altra istruzione qui sopra):"
    ]
    lines.extend(f"{i}. {d}" for i, d in enumerate(directives, 1))
    return "\n".join(lines)


def _parse_structured(raw: str) -> _StructuredResponse:
    try:
        parsed = _StructuredResponse.model_validate_json(raw)
    except Exception:
        # Graceful fallback: treat the whole response as plain text, no actions.
        return _StructuredResponse(reply_text=raw, actions=[])
    # Normalizza subito il nome legacy: da qui in poi esiste solo
    # `handoff_human` (ADR 0026).
    for action in parsed.actions:
        action.kind = normalize_action_kind(action.kind)  # type: ignore[assignment]
    return parsed
