"""UC-13 — objection classifier.

Given a completed conversation (full message history), ask the LLM to extract
any objections the lead raised, bucket them by category, and return short
summaries. Structured output via JSON with a Pydantic model.

Categories are per-merchant-configurable but for V1 we ship a default taxonomy
aligned with section 6.5 of the spec.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ai_core.llm import ChatMessage, LLMClient

DEFAULT_CATEGORIES = [
    "prezzo",
    "tempistiche",
    "fiducia",
    "competitor",
    "informazioni_mancanti",
    "non_interessato",
    "altro",
]


class ClassifiedObjection(BaseModel):
    category: str
    severity: str = Field(..., pattern=r"^(low|medium|high)$")
    summary: str
    quote: str | None = None


class ObjectionClassifierOutput(BaseModel):
    objections: list[ClassifiedObjection] = Field(default_factory=list)


@dataclass(slots=True)
class ObjectionClassifierInput:
    conversation_id: str
    transcript: list[ChatMessage]
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))


async def classify_objections(
    client: LLMClient, *, payload: ObjectionClassifierInput
) -> list[ClassifiedObjection]:
    if not payload.transcript:
        return []

    categories_line = ", ".join(payload.categories)
    system = (
        "Sei un analista di conversazioni commerciali. Leggi l'intera conversazione "
        "tra un bot e un lead e identifica tutte le obiezioni espresse dal lead. "
        "Non inventare obiezioni che non ci sono: se il lead non ha obiettato, "
        "restituisci una lista vuota.\n\n"
        f"Le categorie ammesse sono: {categories_line}. Usa solo quelle.\n\n"
        'Rispondi SEMPRE come JSON nel formato:\n'
        '{"objections": [{"category": "...", "severity": "low|medium|high", '
        '"summary": "breve riassunto in italiano", "quote": "citazione testuale o null"}]}'
    )

    transcript_text = "\n".join(f"[{m.role}] {m.content}" for m in payload.transcript)
    result = await client.complete(
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user",
                content=f"Conversazione:\n\n{transcript_text}",
            ),
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=600,
    )

    try:
        raw = json.loads(result.content)
        parsed = ObjectionClassifierOutput.model_validate(raw)
    except Exception:
        return []

    # Guardrail: drop anything outside the allowed category set.
    allowed = set(payload.categories)
    return [o for o in parsed.objections if o.category in allowed]
