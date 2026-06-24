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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ai_core.llm import ChatMessage, LLMClient, OpenAIClient
from db import session_scope
from db.models import FTModel
from shared import IntegrationError, get_logger, get_settings

logger = get_logger(__name__)

MAX_SAMPLES = 50
DEFAULT_PASS_MARGIN = 0.05  # FT may trail baseline by at most this and still pass.

# Parole che, nel turno assistant atteso, segnalano che la risposta dovrebbe
# proporre/confermare un appuntamento. Usate per derivare il "ground truth" del
# sample: se l'assistant umano ha parlato di slot, ci aspettiamo che il modello
# emetta un'azione di booking.
_BOOKING_KEYWORDS = (
    "appuntamento",
    "prenot",
    "disponibil",
    "slot",
    "fissare",
    "fissiamo",
    "calendario",
)
# Azioni che contano come "gestione booking" nel JSON di risposta del modello.
_BOOKING_ACTIONS = {"book_slot", "propose_slots", "reschedule_slot"}

_EVAL_SYSTEM = (
    'Rispondi con un JSON: {"reply_text": "<risposta>", "actions": []}. '
    "reply_text non deve mai essere vuoto. Quando il cliente vuole prenotare, "
    'aggiungi in actions un oggetto {"kind": "propose_slots"} o '
    '{"kind": "book_slot"}.'
)


@dataclass(slots=True, frozen=True)
class EvalSample:
    prompt: str
    expects_booking: bool


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


def has_booking_action(content: str) -> bool:
    """True se il JSON di risposta contiene un'azione di gestione appuntamento."""
    try:
        obj = json.loads(content)
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    actions = obj.get("actions")
    if not isinstance(actions, list):
        return False
    for action in actions:
        if isinstance(action, dict) and action.get("kind") in _BOOKING_ACTIONS:
            return True
    return False


def _expects_booking(assistant_text: str) -> bool:
    lowered = assistant_text.lower()
    return any(kw in lowered for kw in _BOOKING_KEYWORDS)


def extract_samples(raw: bytes, *, limit: int = MAX_SAMPLES) -> list[EvalSample]:
    """Pull (user-prompt, expects_booking) samples from an OpenAI-format JSONL
    dataset. `expects_booking` è derivato dal turno assistant atteso (#17)."""
    samples: list[EvalSample] = []
    for raw_line in raw.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception as e:
            logger.debug("ft.eval.bad_dataset_line", error=str(e))
            continue
        prompt: str | None = None
        assistant: str = ""
        for msg in record.get("messages", []):
            role, content = msg.get("role"), msg.get("content")
            if not isinstance(content, str):
                continue
            if role == "user" and prompt is None:
                prompt = content
            elif role == "assistant":
                assistant = content
        if prompt is not None:
            samples.append(EvalSample(prompt=prompt, expects_booking=_expects_booking(assistant)))
        if len(samples) >= limit:
            break
    return samples


def extract_prompts(raw: bytes, *, limit: int = MAX_SAMPLES) -> list[str]:
    """Pull user-turn prompts from an OpenAI-format JSONL dataset (thin wrapper
    over `extract_samples`, kept for callers/tests that only need the prompts)."""
    return [s.prompt for s in extract_samples(raw, limit=limit)]


@dataclass(slots=True, frozen=True)
class ModelScores:
    reply_quality: float  # frazione di risposte JSON valide con reply_text
    booking_recall: float  # frazione di sample booking-attesi con azione booking


async def score_model(client: LLMClient, samples: list[EvalSample]) -> ModelScores:
    """Mean reply-quality + booking-recall over the samples (each 0..1)."""
    if not samples:
        return ModelScores(reply_quality=0.0, booking_recall=0.0)
    quality_total = 0.0
    booking_hits = 0
    booking_expected = 0
    for sample in samples:
        try:
            res = await client.complete(
                messages=[
                    ChatMessage(role="system", content=_EVAL_SYSTEM),
                    ChatMessage(role="user", content=sample.prompt),
                ],
                response_format={"type": "json_object"},
            )
            quality_total += reply_quality(res.content)
            if sample.expects_booking:
                booking_expected += 1
                if has_booking_action(res.content):
                    booking_hits += 1
        except Exception as e:  # one bad sample shouldn't abort the eval
            logger.warning("ft.eval.sample_failed", error=str(e))
    # Senza sample booking-attesi il recall è neutro (1.0) per non penalizzare.
    booking_recall = (booking_hits / booking_expected) if booking_expected else 1.0
    return ModelScores(
        reply_quality=quality_total / len(samples),
        booking_recall=booking_recall,
    )


async def evaluate_model(
    ctx: dict[str, Any],
    ft_model_row_id: str,
    *,
    test_set_path: str | None = None,
    pass_margin: float = DEFAULT_PASS_MARGIN,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise IntegrationError("OPENAI_API_KEY not configured", error_code="openai_not_configured")

    async with session_scope() as session:
        row = await session.get(FTModel, UUID(ft_model_row_id))
        if row is None or row.status != "ready":
            raise IntegrationError(
                "FT model not ready for evaluation",
                error_code="ft_not_ready",
                ft_model_row_id=ft_model_row_id,
            )
        provider_model_id = row.provider_model_id

    # Held-out set obbligatorio per un giudizio valido: usiamo SOLO l'eval split
    # (#17). Se manca (run troppo piccola per lo split) NON ripieghiamo sul
    # training set — sarebbe overfitting mascherato da pass — e marchiamo lo
    # stato come non deployabile (#18).
    path = test_set_path
    samples: list[EvalSample] = []
    if path:
        from integrations import SupabaseStorage

        storage = SupabaseStorage(
            project_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_ft_bucket,
        )
        samples = extract_samples(await storage.download(path))

    new_status = "evaluated"
    if not samples:
        # Niente held-out set: eval saltata. Stato dedicato, fuori dal gate di
        # deploy (DEPLOYABLE_STATUSES in handlers.py) così non finisce in prod.
        metrics = {
            "method": "heldout_v1",
            "error": "no_test_data",
            "pass": False,
            "evaluated_at": datetime.now(tz=UTC).isoformat(),
        }
        passed = False
        new_status = "eval_skipped"
    else:
        baseline = OpenAIClient(api_key=settings.openai_api_key, model=settings.llm_model_default)
        ft = OpenAIClient(api_key=settings.openai_api_key, model=provider_model_id)
        baseline_scores = await score_model(baseline, samples)
        ft_scores = await score_model(ft, samples)
        # Gate: l'FT deve reggere il confronto sia sulla validità strutturale sia
        # sulla gestione booking (#17), entro un piccolo margine.
        passed = (
            ft_scores.reply_quality >= baseline_scores.reply_quality - pass_margin
            and ft_scores.booking_recall >= baseline_scores.booking_recall - pass_margin
        )
        metrics = {
            "method": "heldout_v1",
            "samples": len(samples),
            "baseline_model": settings.llm_model_default,
            "baseline_score": round(baseline_scores.reply_quality, 4),
            "ft_score": round(ft_scores.reply_quality, 4),
            "baseline_booking_recall": round(baseline_scores.booking_recall, 4),
            "ft_booking_recall": round(ft_scores.booking_recall, 4),
            "pass_margin": pass_margin,
            "pass": passed,
            "evaluated_at": datetime.now(tz=UTC).isoformat(),
        }

    async with session_scope() as session:
        row = await session.get(FTModel, UUID(ft_model_row_id))
        if row is not None:
            row.evaluation = {**(row.evaluation or {}), **metrics}
            row.status = new_status

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

    logger.info(
        "ft.evaluate.done",
        ft_model_row_id=ft_model_row_id,
        passed=passed,
        eval_status=new_status,
        **{
            k: metrics[k]
            for k in ("baseline_score", "ft_score", "baseline_booking_recall", "ft_booking_recall")
            if k in metrics
        },
    )
    return {"ft_model_row_id": ft_model_row_id, "status": new_status, **metrics}
