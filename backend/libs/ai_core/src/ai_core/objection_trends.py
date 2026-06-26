"""S-08 — Objection trend detection and rebuttal suggestion.

Analyzes the merchant's objection history to surface categories that are
growing (>20% week-over-week) and optionally generates a suggested rebuttal
improvement for each trending category via an LLM call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai_core.llm import ChatMessage, LLMClient

_GROWTH_THRESHOLD = 0.20  # 20% week-over-week growth → trending


@dataclass(slots=True)
class ObjectionTrend:
    category: str
    count_current_week: int
    count_prior_week: int
    growth_pct: float  # e.g. 0.35 = 35%
    is_trending: bool
    suggested_rebuttal: str | None = None


def compute_trends(
    *,
    current_week: dict[str, int],
    prior_week: dict[str, int],
) -> list[ObjectionTrend]:
    """Compute growth rates across objection categories.

    Args:
        current_week: {category: count} for the current 7 days.
        prior_week: {category: count} for the prior 7 days.

    Returns:
        List of ObjectionTrend sorted by growth_pct descending.
    """
    all_categories = set(current_week) | set(prior_week)
    trends: list[ObjectionTrend] = []

    for cat in all_categories:
        curr = current_week.get(cat, 0)
        prior = prior_week.get(cat, 0)

        if prior == 0:
            # New category this week — growth is effectively infinite; cap display.
            growth = 1.0 if curr > 0 else 0.0
        else:
            growth = (curr - prior) / prior

        trends.append(
            ObjectionTrend(
                category=cat,
                count_current_week=curr,
                count_prior_week=prior,
                growth_pct=round(growth, 4),
                is_trending=growth >= _GROWTH_THRESHOLD and curr > 0,
            )
        )

    trends.sort(key=lambda t: t.growth_pct, reverse=True)
    return trends


async def suggest_rebuttal(
    client: LLMClient,
    *,
    category: str,
    merchant_context: str | None = None,
    sample_quotes: list[str] | None = None,
) -> str:
    """Generate a suggested rebuttal script for a trending objection category.

    The suggestion is returned as a short Italian paragraph the agent can adapt.
    Fails open (returns empty string) if the LLM call errors.
    """
    quotes_section = ""
    if sample_quotes:
        formatted = "\n".join(f"- {q}" for q in sample_quotes[:5])
        quotes_section = f"\n\nEsempi di obiezioni reali:\n{formatted}"

    context_section = ""
    if merchant_context:
        context_section = f"\n\nContesto del merchant:\n{merchant_context}"

    prompt = (
        f"Sei un esperto di vendite via WhatsApp. La categoria di obiezione più frequente "
        f"è: **{category}**. Scrivi un breve script (2-3 frasi) che l'agente AI può usare "
        f"per rispondere a questa obiezione in modo empatico, autentico e persuasivo. "
        f"Usa un tono conversazionale da WhatsApp, non formale.{quotes_section}{context_section}"
    )

    try:
        result = await client.complete(
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=0.7,
            max_tokens=200,
        )
        return result.content.strip()
    except Exception:
        return ""
