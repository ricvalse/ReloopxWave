"""S-06 — Thompson Sampling bandit for A/B variant selection.

Replaces uniform hash-based variant assignment for new leads when
AB_THOMPSON_SAMPLING_ENABLED is True in the config cascade.

Each variant is modelled as a Bernoulli arm with a Beta(α, β) posterior where:
  α = success_count + 1   (Beta prior α=1 → uniform over [0,1])
  β = (total - success) + 1

We draw one sample per variant and select the arm with the highest draw,
which naturally balances exploration vs. exploitation as evidence accumulates.
"""

from __future__ import annotations

import random
from typing import Any


def thompson_sample(
    variants: list[dict[str, Any]],
    *,
    variant_wins: dict[str, int],
    variant_totals: dict[str, int],
) -> str:
    """Return the variant_id selected by Thompson Sampling.

    Args:
        variants: list of variant dicts from ABExperiment.variants (each has "id").
        variant_wins: conversions (successes) per variant_id observed so far.
        variant_totals: total assignments per variant_id observed so far.

    Returns:
        variant_id of the selected arm.
    """
    best_id: str | None = None
    best_sample = -1.0

    for v in variants:
        vid = str(v.get("id", "default"))
        wins = variant_wins.get(vid, 0)
        total = variant_totals.get(vid, 0)
        failures = max(0, total - wins)

        alpha = wins + 1
        beta = failures + 1
        sample = random.betavariate(alpha, beta)

        if sample > best_sample:
            best_sample = sample
            best_id = vid

    if best_id is None:
        best_id = str(variants[0].get("id", "default")) if variants else "default"
    return best_id
