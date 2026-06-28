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

from ai_core.llm import ChatMessage, LLMClient
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
    "escalate_human",
    "none",
]

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
            escalate_keywords_matched=_has_critical_objection(user_message),
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
        system_parts = [ctx.system_prompt, _RESPONSE_SCHEMA_HINT]
        # Qualification context (internal — never repeat the number to the lead):
        # gives the model the current score + the merchant's configured advance
        # threshold so `move_pipeline` fires in line with the merchant's setting.
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
        messages = [ChatMessage(role="system", content="\n\n".join(system_parts))]
        messages.extend(ctx.history)
        messages.append(ChatMessage(role="user", content=user_message))
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
        # — drop them along with any action the automation node didn't allow.
        actions = [a for a in parsed.actions if a.kind not in READ_TOOL_KINDS]
        if allowed_actions is not None:
            actions = [a for a in actions if a.kind in allowed_actions]
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
        system_parts = [ctx.system_prompt, _RESPONSE_SCHEMA_HINT]
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


_RESPONSE_SCHEMA_HINT = (
    "Rispondi SEMPRE con un JSON valido che rispetta esattamente questo schema:\n"
    "{\n"
    '  "reply_text": "<testo da inviare all\'utente>",\n'
    '  "actions": [\n'
    '    {"kind": "check_availability|lookup_appointment|propose_slots|book_slot|reschedule_slot|cancel_slot|move_pipeline|update_score|escalate_human|none", "payload": {}}\n'
    "  ]\n"
    "}\n"
    "`reply_text` non deve mai essere vuoto. `actions` può essere lista vuota.\n"
    "\n"
    "STRUMENTI DI LETTURA (check_availability, lookup_appointment): se ti servono "
    "dati reali per rispondere con verità (è libero quello slot? che appuntamento "
    "ha il cliente?), EMETTI lo strumento e metti in `reply_text` solo una frase di "
    "attesa ('controllo subito', 'un attimo che verifico'). Riceverai il RISULTATO "
    "STRUMENTI e poi scriverai la risposta definitiva con i dati veri. NON inventare "
    "disponibilità o dettagli che non hai verificato.\n"
    "\n"
    "Emetti le azioni SOLO quando i criteri sono soddisfatti, riempiendo il payload:\n"
    "\n"
    '- "check_availability": quando l\'utente chiede se un orario/giorno è libero o '
    "quali disponibilità ci sono. payload: {\n"
    '    "preferred_start_iso": "<ISO8601 se ha indicato un orario preciso, opzionale>",\n'
    '    "lookahead_days": <numero giorni da guardare, opzionale>\n'
    "  }\n"
    '- "lookup_appointment": quando l\'utente fa riferimento a "il mio appuntamento" '
    "(per spostarlo, annullarlo o chiederne i dettagli) e ti serve sapere qual è. "
    "payload: {}\n"
    '- "propose_slots": quando l\'utente vuole prenotare ma NON ha ancora indicato '
    'un orario preciso (chiede disponibilità, "quando siete liberi?"). Mostra gli '
    "slot liberi così l'utente ne sceglie uno. payload: {} (reply_text può anticipare "
    '"ti mostro le disponibilità").\n'
    '- "book_slot": quando l\'utente vuole prenotare/fissare un appuntamento o '
    "accetta uno slot proposto. payload: {\n"
    '    "preferred_start_iso": "<ISO8601, es. 2026-06-03T15:00:00, se l\'utente indica data/ora>",\n'
    '    "service_id": "<UUID del servizio scelto, presente nel prompt se il merchant ha servizi configurati>",\n'
    '    "contact_fields": {"name": "<se noto>", "email": "<se noto>"}\n'
    "  }\n"
    '- "reschedule_slot": quando l\'utente vuole SPOSTARE/cambiare un appuntamento '
    "già fissato. payload: {\n"
    '    "preferred_start_iso": "<ISO8601 della nuova data/ora, se indicata>"\n'
    "  }\n"
    '- "cancel_slot": quando l\'utente vuole ANNULLARE/disdire un appuntamento già '
    "fissato. payload: {}\n"
    '- "move_pipeline": quando il lead è chiaramente qualificato e pronto ad '
    "avanzare (intenzione forte, budget/tempistiche confermati). payload: {\n"
    '    "stage": "<nome stage target, opzionale>"\n'
    "  }\n"
    '- "update_score": ad OGNI turno in cui il messaggio rivela qualcosa di '
    "rilevante sul lead. payload: {\n"
    '    "signals": { ... usa SOLO queste chiavi booleane, true se vere in QUESTO '
    "messaggio ... }\n"
    "  }\n"
    "  chiavi valide per signals: has_name, has_email, has_budget, has_timeline, "
    "asked_for_booking, objection_price, objection_trust, objection_competitor, "
    "dropped_off, profanity.\n"
    '- "escalate_human": quando l\'utente è arrabbiato, minaccia reclami/azioni '
    "legali, o chiede esplicitamente una persona. payload: {\n"
    '    "reason": "<motivo breve, es. cliente_arrabbiato/richiesta_umano>",\n'
    '    "customer_message_summary": "<1-2 frasi che riassumono cosa serve al '
    "cliente, per l'operatore che prende in carico>\"\n"
    "  }\n"
    '- "none": negli altri casi.\n'
    "Puoi emettere più azioni nello stesso turno (es. update_score + book_slot).\n"
    "\n"
    "IMPORTANTE — niente false conferme: per book_slot / reschedule_slot / "
    "cancel_slot / propose_slots la conferma reale (con l'esito vero: prenotato, "
    "slot occupato + alternative, spostato...) viene inviata dal sistema DOPO il "
    "tuo messaggio. Quindi in `reply_text` NON dire che è già fatto ('ho prenotato', "
    "'appuntamento spostato'): scrivi una frase di passaggio ('procedo subito e ti "
    "confermo', 'un attimo che verifico'). Se vuoi essere certo della disponibilità "
    "prima di proporre un orario, usa check_availability."
)

CRITICAL_KEYWORDS = (
    "reclamo",
    "avvocato",
    "truffa",
    "rimborso immediato",
    "denuncia",
    "concorrenza",
)


def _has_critical_objection(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in CRITICAL_KEYWORDS)


def _parse_structured(raw: str) -> _StructuredResponse:
    try:
        return _StructuredResponse.model_validate_json(raw)
    except Exception:
        # Graceful fallback: treat the whole response as plain text, no actions.
        return _StructuredResponse(reply_text=raw, actions=[])
