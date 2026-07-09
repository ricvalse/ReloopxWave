"""UC-08 — the playground must preview the EXACT live-flow behavior.

These tests pin the parity contract: the runner builds its system prompt from
the canonical `build_cascade_system_prompt` (no caller override), runs as the
control arm (`variant_id is None`) with a fresh-contact lead (`lead_score == 0`),
and resolves `hot_threshold` from the same config cascade as a real turn.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_core import playground as pg
from ai_core.playground import PlaygroundMessage, PlaygroundRequest, PlaygroundRunner
from ai_core.playground_sim import PlaygroundLeadState
from config_resolver import ConfigKey


class _FakeSessionCtx:
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _fake_resolver(values: dict):
    class _R:
        def __init__(self, session: object) -> None:
            pass

        async def resolve(self, key, *, merchant_id):
            return values.get(key)

        async def resolve_all(self, *, merchant_id):
            # Mirror the real bag: flat dict keyed by ConfigKey.value (dotted).
            return {(k.value if isinstance(k, ConfigKey) else k): v for k, v in values.items()}

    return _R


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    sentinel_session = object()
    monkeypatch.setattr(pg, "tenant_session", lambda ctx: _FakeSessionCtx(sentinel_session))
    monkeypatch.setattr(pg, "ConfigResolver", _fake_resolver({ConfigKey.SCORING_HOT_THRESHOLD: 65}))
    # No KB by default → neither inline injection nor RAG (keeps these tests
    # focused on the prompt/orchestrator wiring).
    monkeypatch.setattr(pg, "kb_estimated_tokens", AsyncMock(return_value=0))

    async def _fake_build(
        *, session, merchant_id, prior_sentiment=None, customer_message=None
    ) -> str:
        captured["prior_sentiment"] = prior_sentiment
        captured["build_merchant_id"] = merchant_id
        captured["customer_message"] = customer_message
        return "PROMPT-CANONICO"

    monkeypatch.setattr(pg, "build_cascade_system_prompt", _fake_build)

    orch = AsyncMock()
    orch.run.return_value = SimpleNamespace(
        reply_text="ciao!",
        actions=[SimpleNamespace(kind="update_score", payload={"n": 1})],
        model="gpt-5-mini",
        tokens_in=10,
        tokens_out=5,
        latency_ms=42,
    )
    runner = PlaygroundRunner(orchestrator=orch, embedder=None)
    return runner, orch, captured


async def test_playground_uses_canonical_cascade_prompt(wiring) -> None:
    runner, orch, captured = wiring
    mid = uuid.uuid4()

    out = await runner.run(
        PlaygroundRequest(
            tenant_id=uuid.uuid4(),
            merchant_id=mid,
            history=[PlaygroundMessage(role="user", content="ciao")],
            user_message="vorrei prenotare",
        )
    )

    # The reply is passed straight through from the orchestrator.
    assert out.reply_text == "ciao!"
    assert out.model == "gpt-5-mini"
    assert out.actions == [{"kind": "update_score", "payload": {"n": 1}}]

    # The prompt comes from the canonical builder, for THIS merchant, with no
    # prior sentiment (no real conversation to adapt to).
    assert captured["build_merchant_id"] == mid
    assert captured["prior_sentiment"] is None

    # The orchestrator received the cascade prompt + the live user message.
    ctx, user_message = orch.run.call_args.args
    assert user_message == "vorrei prenotare"
    assert ctx.system_prompt == "PROMPT-CANONICO"


async def test_playground_runs_as_control_arm_fresh_contact(wiring) -> None:
    runner, orch, _ = wiring

    await runner.run(
        PlaygroundRequest(
            tenant_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            history=[],
            user_message="ciao",
        )
    )

    ctx = orch.run.call_args.args[0]
    # No A/B assignment (control arm), no real lead, threshold from the cascade.
    assert ctx.variant_id is None
    assert ctx.lead_id is None
    assert ctx.lead_score == 0
    assert ctx.hot_threshold == 65


async def test_playground_normalizes_agent_role_like_live_flow(wiring) -> None:
    runner, orch, _ = wiring

    await runner.run(
        PlaygroundRequest(
            tenant_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            history=[
                PlaygroundMessage(role="user", content="ciao"),
                # A business-side reply (composer / phone echo) is stored as
                # "agent" — the LLM only accepts "assistant" (parity with the
                # live turn's _to_chat_history).
                PlaygroundMessage(role="agent", content="buongiorno"),
            ],
            user_message="vorrei info",
        )
    )

    ctx = orch.run.call_args.args[0]
    assert [m.role for m in ctx.history] == ["user", "assistant"]


async def test_playground_carries_incoming_state(wiring) -> None:
    runner, orch, captured = wiring

    out = await runner.run(
        PlaygroundRequest(
            tenant_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            history=[],
            user_message="ciao",
            state=PlaygroundLeadState.from_dict(
                {"lead_score": 50, "lead_sentiment": "positive", "turn_count": 2}
            ),
        )
    )

    # Prior-turn sentiment adapts the prompt (parity with live flow).
    assert captured["prior_sentiment"] == "positive"
    # The carried score feeds the orchestrator → escalation can warm up over turns.
    assert orch.run.call_args.args[0].lead_score == 50
    # State evolves and is returned for the next turn.
    assert out.state["turn_count"] == 3
    # Dry-run output is populated.
    assert out.bubbles and out.bubbles[0]["text"]
    assert any(e["kind"] == "update_score" for e in out.events)


async def test_playground_override_rules_ride_on_system_prompt(wiring) -> None:
    runner, orch, _ = wiring

    await runner.run(
        PlaygroundRequest(
            tenant_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            history=[],
            user_message="ciao",
            override_rules=["Non offrire mai sconti", "  "],
        )
    )

    # The tester's rules are appended to the canonical prompt for this turn.
    ctx = orch.run.call_args.args[0]
    assert ctx.system_prompt.startswith("PROMPT-CANONICO")
    assert "Regole aggiuntive dal tester (playground):" in ctx.system_prompt
    assert "- Non offrire mai sconti" in ctx.system_prompt


async def test_playground_records_turn_sentiment_in_returned_state(wiring) -> None:
    runner, _orch, _ = wiring

    class _Sentiment:
        async def analyze(self, *, merchant_id, tenant_id, text) -> str:
            return "negative"

    runner._sentiment = _Sentiment()

    out = await runner.run(
        PlaygroundRequest(
            tenant_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            history=[],
            user_message="che servizio pessimo",
        )
    )

    # This turn's sentiment is stored for the NEXT turn's adaptation.
    assert out.state["lead_sentiment"] == "negative"


async def test_playground_rag_uses_hyde_rerank_and_skips_gap_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preview must run the SAME RAG pipeline as the live turn: an llm_client
    (HyDE + re-ranking) and the merchant's RAG config — but never log kb_gaps,
    since the playground is a dry-run."""
    sentinel_session = object()
    monkeypatch.setattr(pg, "tenant_session", lambda ctx: _FakeSessionCtx(sentinel_session))
    # Large KB (> inline threshold) → the RAG path, not inline injection.
    monkeypatch.setattr(
        pg, "kb_estimated_tokens", AsyncMock(return_value=pg.KB_INLINE_MAX_TOKENS + 1)
    )
    monkeypatch.setattr(
        pg,
        "ConfigResolver",
        _fake_resolver(
            {
                ConfigKey.SCORING_HOT_THRESHOLD: 65,
                ConfigKey.RAG_TOP_K: 4,
                ConfigKey.RAG_MIN_SCORE: 0.6,
                ConfigKey.RAG_HYDE_ENABLED: True,
                ConfigKey.RAG_RERANK_ENABLED: True,
                ConfigKey.RAG_RERANK_TOP_K: 3,
                ConfigKey.RAG_FRESHNESS_DECAY: 0.02,
            }
        ),
    )

    async def _fake_build(
        *, session, merchant_id, prior_sentiment=None, customer_message=None
    ) -> str:
        return "PROMPT"

    monkeypatch.setattr(pg, "build_cascade_system_prompt", _fake_build)

    captured: dict = {}

    class _FakeRAG:
        def __init__(self, session, embedder, *, llm_client=None) -> None:
            captured["llm_client"] = llm_client

        async def retrieve(self, query, *, merchant_id, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            return [SimpleNamespace(chunk_id=uuid.uuid4(), score=0.72, content="chunk body")]

    monkeypatch.setattr(pg, "RAGEngine", _FakeRAG)

    orch = AsyncMock()
    orch._router = SimpleNamespace(_settings=SimpleNamespace(openai_api_key="sk-test"))
    orch.run.return_value = SimpleNamespace(
        reply_text="ok", actions=[], model="gpt-5-mini", tokens_in=1, tokens_out=1, latency_ms=1
    )

    runner = PlaygroundRunner(orchestrator=orch, embedder=object())  # non-None → RAG runs

    out = await runner.run(
        PlaygroundRequest(
            tenant_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            history=[],
            user_message="come funziona la candidatura?",
        )
    )

    # HyDE/re-ranking client is wired (raw-query-only retrieval was the bug).
    assert captured["llm_client"] is not None
    kw = captured["kwargs"]
    assert kw["hyde_enabled"] is True
    assert kw["rerank_enabled"] is True
    assert kw["top_k"] == 4
    assert kw["min_score"] == 0.6
    assert kw["rerank_top_k"] == 3
    assert kw["freshness_decay"] == 0.02
    # Dry-run invariant: the playground must not write to kb_gaps.
    assert kw["log_gaps"] is False
    # The retrieved chunk is surfaced to the UI and injected into the orchestrator ctx.
    assert out.retrieved_chunks and out.retrieved_chunks[0]["score"] == 0.72
    ctx, _ = orch.run.call_args.args
    assert ctx.kb_chunks and ctx.kb_chunks[0].content == "chunk body"


async def test_playground_small_kb_is_injected_whole_no_rag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the whole KB is under the inline threshold, every chunk is injected
    into the prompt and RAG (vector search) is skipped entirely — so no
    retrieval miss can drop a fact that is in the KB."""
    sentinel_session = object()
    monkeypatch.setattr(pg, "tenant_session", lambda ctx: _FakeSessionCtx(sentinel_session))
    monkeypatch.setattr(pg, "ConfigResolver", _fake_resolver({ConfigKey.SCORING_HOT_THRESHOLD: 65}))

    async def _fake_build(
        *, session, merchant_id, prior_sentiment=None, customer_message=None
    ) -> str:
        return "PROMPT"

    monkeypatch.setattr(pg, "build_cascade_system_prompt", _fake_build)

    # Small KB → inline path.
    monkeypatch.setattr(pg, "kb_estimated_tokens", AsyncMock(return_value=500))
    all_chunks = [
        SimpleNamespace(chunk_id=uuid.uuid4(), score=1.0, content="Domanda: X / Risposta: Y"),
        SimpleNamespace(chunk_id=uuid.uuid4(), score=1.0, content="Documento: Z"),
    ]
    monkeypatch.setattr(pg, "kb_all_chunks", AsyncMock(return_value=all_chunks))

    # RAGEngine must NOT be constructed on the inline path.
    def _boom(*a, **k):  # pragma: no cover - asserts it's never called
        raise AssertionError("RAGEngine must not be used when the KB is injected whole")

    monkeypatch.setattr(pg, "RAGEngine", _boom)

    orch = AsyncMock()
    orch.run.return_value = SimpleNamespace(
        reply_text="ok", actions=[], model="gpt-5-mini", tokens_in=1, tokens_out=1, latency_ms=1
    )
    runner = PlaygroundRunner(orchestrator=orch, embedder=object())

    out = await runner.run(
        PlaygroundRequest(
            tenant_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            history=[],
            user_message="qualsiasi domanda",
        )
    )

    # All chunks reach the orchestrator context and the UI, verbatim.
    ctx, _ = orch.run.call_args.args
    assert [c.content for c in ctx.kb_chunks] == [c.content for c in all_chunks]
    assert len(out.retrieved_chunks) == 2
    assert all(rc["score"] == 1.0 for rc in out.retrieved_chunks)
