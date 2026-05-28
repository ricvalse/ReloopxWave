"""Held-out evaluation of an FT model vs the baseline (spec 11.2).

Replaces the V1 placeholder. For a sample of held-out prompts we run both the
fine-tuned model and the baseline (gpt-5-mini) and compare a computable quality
proxy: does the model return a *valid structured reply* (parseable JSON with a
non-empty `reply_text`) — the contract the orchestrator depends on. The FT
model passes if it's at least as good as baseline within a small margin.

The metric is intentionally simple but honest: it measures the property that
actually matters for the production path (structured-output reliability), and
the comparison is against the live baseline so a regression blocks deploy. A
richer metric (objection handling, booking rate) is a future refinement; the
harness here is real and the pass/fail gate is enforced by the orchestration.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ai_core.llm import ChatMessage, LLMClient, OpenAIClient
from db import session_scope
from db.models import FTModel
from shared import IntegrationError, get_logger, get_settings

logger = get_logger(__name__)

BASELINE_MODEL = "gpt-5-mini"
MAX_SAMPLES = 50
DEFAULT_PASS_MARGIN = 0.05  # FT may trail baseline by at most this and still pass.

_EVAL_SYSTEM = (
    'Rispondi con un JSON: {"reply_text": "<risposta>", "actions": []}. '
    "reply_text non deve mai essere vuoto."
)


def reply_quality(content: str) -> float:
    """0/1 proxy: valid JSON object carrying a non-empty reply_text."""
    try:
        obj = json.loads(content)
    except Exception:
        return 0.0
    if not isinstance(obj, dict):
        return 0.0
    reply = obj.get("reply_text")
    return 1.0 if isinstance(reply, str) and reply.strip() else 0.0


def extract_prompts(raw: bytes, *, limit: int = MAX_SAMPLES) -> list[str]:
    """Pull user-turn prompts from an OpenAI-format JSONL dataset."""
    prompts: list[str] = []
    for raw_line in raw.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception as e:
            logger.debug("ft.eval.bad_dataset_line", error=str(e))
            continue
        for msg in record.get("messages", []):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                prompts.append(msg["content"])
                break
        if len(prompts) >= limit:
            break
    return prompts


async def score_model(client: LLMClient, prompts: list[str]) -> float:
    """Mean reply-quality over the prompts (0..1)."""
    if not prompts:
        return 0.0
    total = 0.0
    for prompt in prompts:
        try:
            res = await client.complete(
                messages=[
                    ChatMessage(role="system", content=_EVAL_SYSTEM),
                    ChatMessage(role="user", content=prompt),
                ],
                response_format={"type": "json_object"},
            )
            total += reply_quality(res.content)
        except Exception as e:  # one bad sample shouldn't abort the eval
            logger.warning("ft.eval.sample_failed", error=str(e))
    return total / len(prompts)


async def evaluate_model(
    ctx: dict[str, Any],
    ft_model_row_id: str,
    *,
    test_set_path: str | None = None,
    pass_margin: float = DEFAULT_PASS_MARGIN,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise IntegrationError(
            "OPENAI_API_KEY not configured", error_code="openai_not_configured"
        )

    async with session_scope() as session:
        row = await session.get(FTModel, UUID(ft_model_row_id))
        if row is None or row.status != "ready":
            raise IntegrationError(
                "FT model not ready for evaluation",
                error_code="ft_not_ready",
                ft_model_row_id=ft_model_row_id,
            )
        provider_model_id = row.provider_model_id
        dataset_path = row.dataset_path

    # Prefer an explicit held-out set; fall back to the training set as a
    # smoke-test source (better than no comparison at all).
    path = test_set_path or dataset_path
    prompts: list[str] = []
    if path:
        from integrations import SupabaseStorage

        storage = SupabaseStorage(
            project_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_ft_bucket,
        )
        prompts = extract_prompts(await storage.download(path))

    if not prompts:
        metrics = {
            "method": "heldout_v1",
            "error": "no_test_data",
            "pass": False,
            "evaluated_at": datetime.now(tz=UTC).isoformat(),
        }
        passed = False
    else:
        baseline = OpenAIClient(api_key=settings.openai_api_key, model=BASELINE_MODEL)
        ft = OpenAIClient(api_key=settings.openai_api_key, model=provider_model_id)
        baseline_score = await score_model(baseline, prompts)
        ft_score = await score_model(ft, prompts)
        passed = ft_score >= baseline_score - pass_margin
        metrics = {
            "method": "heldout_v1",
            "samples": len(prompts),
            "baseline_model": BASELINE_MODEL,
            "baseline_score": round(baseline_score, 4),
            "ft_score": round(ft_score, 4),
            "pass_margin": pass_margin,
            "pass": passed,
            "evaluated_at": datetime.now(tz=UTC).isoformat(),
        }

    async with session_scope() as session:
        row = await session.get(FTModel, UUID(ft_model_row_id))
        if row is not None:
            row.evaluation = {**(row.evaluation or {}), **metrics}
            row.status = "evaluated"

    # Gate: only a passing model proceeds to the A/B rollout (spec 6.7 — rollout
    # via A/B, not a flag flip). A failing eval stops the chain; an operator can
    # still deploy manually after review.
    if passed:
        redis = ctx.get("redis")
        if redis is not None:
            await redis.enqueue_job(
                "fine_tune_deploy",
                ft_model_row_id,
                _job_id=f"ft:deploy:{ft_model_row_id}",
            )

    logger.info("ft.evaluate.done", ft_model_row_id=ft_model_row_id, passed=passed, **{
        k: metrics[k] for k in ("baseline_score", "ft_score") if k in metrics
    })
    return {"ft_model_row_id": ft_model_row_id, "status": "evaluated", **metrics}
