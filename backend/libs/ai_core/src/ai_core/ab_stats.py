"""UC-09 — A/B significance test (pure, no numpy/scipy dependency).

A two-proportion z-test on the primary-metric conversion rate of two variants.
We keep it dependency-free with `math.erf` for the normal CDF; for the sample
sizes a single merchant produces this is more than accurate enough to answer
"is the difference unlikely to be noise?".
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class SignificanceResult:
    winner: str | None
    p_value: float | None
    significant: bool
    confidence: float  # 1 - p_value, clamped to [0, 1]; 0.0 when undetermined


def _normal_sf(z: float) -> float:
    """Survival function (upper tail) of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_proportion_ztest(conv_a: int, n_a: int, conv_b: int, n_b: int) -> float | None:
    """Two-sided p-value for H0: rate_a == rate_b. None if a sample is empty or
    the pooled rate is degenerate (0% or 100% in both arms)."""
    if n_a <= 0 or n_b <= 0:
        return None
    p_pool = (conv_a + conv_b) / (n_a + n_b)
    if p_pool <= 0.0 or p_pool >= 1.0:
        return None
    se = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n_a + 1.0 / n_b))
    if se == 0.0:
        return None
    z = ((conv_a / n_a) - (conv_b / n_b)) / se
    return 2.0 * _normal_sf(abs(z))


def evaluate_significance(
    variants: list[tuple[str, int, int]], *, alpha: float = 0.05
) -> SignificanceResult:
    """Given [(variant_id, conversions, assignments), ...], compare the two
    highest-traffic arms and report the better-converting one as winner when the
    difference is significant at `alpha`.

    Restricted to exactly the top-two arms so the result is well-defined for the
    common 2-variant test; >2 arms compares the two with the most assignments.
    """
    eligible = [(vid, c, n) for vid, c, n in variants if n > 0]
    if len(eligible) < 2:
        return SignificanceResult(None, None, False, 0.0)

    top = sorted(eligible, key=lambda t: t[2], reverse=True)[:2]
    (id_a, c_a, n_a), (id_b, c_b, n_b) = top
    p = two_proportion_ztest(c_a, n_a, c_b, n_b)
    if p is None:
        return SignificanceResult(None, None, False, 0.0)

    significant = p < alpha
    rate_a, rate_b = c_a / n_a, c_b / n_b
    winner = (id_a if rate_a >= rate_b else id_b) if significant else None
    return SignificanceResult(
        winner=winner,
        p_value=p,
        significant=significant,
        confidence=max(0.0, min(1.0, 1.0 - p)),
    )
