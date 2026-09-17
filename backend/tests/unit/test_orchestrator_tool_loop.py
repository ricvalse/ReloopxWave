"""Amalia-style tool-use loop in the orchestrator (CORE).

The orchestrator, given a tool executor and >1 iteration, must: call a read
tool when the model asks, reinject the real result, let the model finish, strip
read-tool actions from the returned actions, and accumulate token/latency totals.
"""

from __future__ import annotations

import json
import uuid

from ai_core.llm import ChatMessage, CompletionResult
from ai_core.orchestrator import (
    ConversationContext,
    ConversationOrchestrator,
    OrchestratorAction,
    ToolResult,
)


class FakeClient:
    """LLMClient that returns a queued list of JSON contents, capturing the
    messages it was called with each time."""

    model = "fake-model"

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, *, messages, response_format=None, temperature=0.3, max_tokens=None):
        self.calls.append(list(messages))
        content = self._contents[min(len(self.calls) - 1, len(self._contents) - 1)]
        return CompletionResult(
            content=content,
            model=self.model,
            tokens_in=10,
            tokens_out=5,
            latency_ms=100,
            raw={},
        )


class FakeRouter:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    async def select(self, req):
        return self._client

    async def fallback(self):
        return None


class FakeToolExecutor:
    def __init__(self, summary: str = "Lo slot richiesto è LIBERO.") -> None:
        self.calls: list[OrchestratorAction] = []
        self._summary = summary

    async def execute_read(self, action, ctx) -> ToolResult:
        self.calls.append(action)
        return ToolResult(kind=action.kind, ok=True, summary=self._summary)


def _ctx() -> ConversationContext:
    return ConversationContext(
        merchant_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        lead_score=10,
        hot_threshold=80,
        system_prompt="sei un assistente",
    )


async def test_single_shot_when_no_executor() -> None:
    client = FakeClient([json.dumps({"reply_text": "ciao", "actions": [{"kind": "none"}]})])
    orch = ConversationOrchestrator(FakeRouter(client))

    resp = await orch.run(_ctx(), "ciao")

    assert len(client.calls) == 1
    assert resp.reply_text == "ciao"
    assert [a.kind for a in resp.actions] == ["none"]


async def test_tool_loop_grounds_then_finishes() -> None:
    contents = [
        json.dumps(
            {
                "reply_text": "controllo subito",
                "actions": [
                    {
                        "kind": "check_availability",
                        "payload": {"preferred_start_iso": "2026-07-01T15:00:00"},
                    }
                ],
            }
        ),
        json.dumps({"reply_text": "Alle 15 è libero, confermo?", "actions": [{"kind": "none"}]}),
    ]
    client = FakeClient(contents)
    executor = FakeToolExecutor(summary="Lo slot (01/07 alle 15:00) è LIBERO.")
    orch = ConversationOrchestrator(FakeRouter(client))

    resp = await orch.run(_ctx(), "alle 15 siete liberi?", tool_executor=executor, max_iterations=3)

    # Two LLM calls (initial + post-tool), one tool execution.
    assert len(client.calls) == 2
    assert len(executor.calls) == 1
    assert executor.calls[0].kind == "check_availability"
    # The second call saw the reinjected tool observation.
    second_call_texts = [m.content for m in client.calls[1]]
    assert any("RISULTATO STRUMENTI" in t for t in second_call_texts)
    assert any("LIBERO" in t for t in second_call_texts)
    # Final reply is the grounded one; read-tool action is stripped.
    assert resp.reply_text == "Alle 15 è libero, confermo?"
    assert [a.kind for a in resp.actions] == ["none"]
    # Tokens accumulate across both calls.
    assert resp.tokens_in == 20
    assert resp.tokens_out == 10
    assert resp.latency_ms == 200


async def test_read_tool_stripped_and_loop_capped() -> None:
    # Model keeps asking for the tool every turn → loop stops at max_iterations
    # and the read-tool action never leaks to the dispatcher.
    forever = json.dumps(
        {"reply_text": "verifico", "actions": [{"kind": "check_availability", "payload": {}}]}
    )
    client = FakeClient([forever])
    executor = FakeToolExecutor()
    orch = ConversationOrchestrator(FakeRouter(client))

    resp = await orch.run(_ctx(), "disponibilità?", tool_executor=executor, max_iterations=2)

    assert len(client.calls) == 2  # capped
    assert len(executor.calls) == 1  # one round-trip before the cap
    assert resp.actions == []  # read tool stripped, nothing for the dispatcher


async def test_unverified_time_proposal_forces_check_availability() -> None:
    # Production evidence (Ghilea, 2026-09-17): the model proposed a concrete
    # day/time straight from playbook text, never calling `check_availability`
    # — even with a single iteration budget that never reaches the model's own
    # tool-use loop. The orchestrator must force a real check before the reply
    # leaves this turn, regenerating it from the grounded observation.
    contents = [
        json.dumps(
            {
                "reply_text": "Le propongo giovedì 24 settembre alle 9:00.",
                "actions": [{"kind": "none"}],
            }
        ),
        json.dumps(
            {
                "reply_text": "Le confermo lunedì 21 settembre alle 10:00.",
                "actions": [{"kind": "none"}],
            }
        ),
    ]
    client = FakeClient(contents)
    executor = FakeToolExecutor(summary="Lunedì 21/09 alle 10:00 è LIBERO.")
    orch = ConversationOrchestrator(FakeRouter(client))

    resp = await orch.run(_ctx(), "la mattina", tool_executor=executor, max_iterations=1)

    # The model never asked for the tool; the orchestrator called it anyway.
    assert len(executor.calls) == 1
    assert executor.calls[0].kind == "check_availability"
    # Two LLM calls: the original (rejected) proposal + the regenerated one.
    assert len(client.calls) == 2
    second_call_texts = [m.content for m in client.calls[1]]
    assert any("RISULTATO STRUMENTI" in t for t in second_call_texts)
    assert any("LIBERO" in t for t in second_call_texts)
    assert resp.reply_text == "Le confermo lunedì 21 settembre alle 10:00."


async def test_unverified_time_proposal_without_executor_is_logged_not_blocked() -> None:
    # No tool executor at all (single-shot mode) → nothing can be verified this
    # turn. The reply passes through unchanged (never rewritten — see the
    # comment at the call site), the caller just gets no grounding guarantee.
    content = json.dumps(
        {"reply_text": "Le propongo giovedì 24 settembre alle 9:00.", "actions": [{"kind": "none"}]}
    )
    client = FakeClient([content])
    orch = ConversationOrchestrator(FakeRouter(client))

    resp = await orch.run(_ctx(), "la mattina")

    assert len(client.calls) == 1
    assert resp.reply_text == "Le propongo giovedì 24 settembre alle 9:00."


async def test_booking_write_this_turn_skips_forced_check() -> None:
    # book_slot fires this turn on a structured preferred_start_iso, not free
    # text — the write path verifies against GHL itself at dispatch time, so
    # forcing another check_availability here would just be a redundant call.
    contents = [
        json.dumps(
            {
                "reply_text": "Procedo con giovedì 24 settembre alle 9:00.",
                "actions": [
                    {"kind": "book_slot", "payload": {"preferred_start_iso": "2026-09-24T09:00:00"}}
                ],
            }
        )
    ]
    client = FakeClient(contents)
    executor = FakeToolExecutor()
    orch = ConversationOrchestrator(FakeRouter(client))

    resp = await orch.run(_ctx(), "prenotami", tool_executor=executor, max_iterations=1)

    assert executor.calls == []
    assert len(client.calls) == 1
    assert [a.kind for a in resp.actions] == ["book_slot"]


async def test_write_actions_pass_through_loop() -> None:
    # A turn that grounds then books: read tool stripped, book_slot kept.
    contents = [
        json.dumps(
            {"reply_text": "controllo", "actions": [{"kind": "check_availability", "payload": {}}]}
        ),
        json.dumps(
            {
                "reply_text": "procedo a prenotare",
                "actions": [
                    {"kind": "book_slot", "payload": {"preferred_start_iso": "2026-07-01T15:00:00"}}
                ],
            }
        ),
    ]
    client = FakeClient(contents)
    executor = FakeToolExecutor()
    orch = ConversationOrchestrator(FakeRouter(client))

    resp = await orch.run(_ctx(), "prenotami alle 15", tool_executor=executor, max_iterations=3)

    assert [a.kind for a in resp.actions] == ["book_slot"]
    assert resp.actions[0].payload["preferred_start_iso"] == "2026-07-01T15:00:00"
