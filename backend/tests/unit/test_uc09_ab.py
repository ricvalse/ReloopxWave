"""UC-09 — hash-based variant assignment is deterministic and respects weights."""
from __future__ import annotations

import uuid
from collections import Counter

from db.repositories.ab import _hash_pick


def test_same_inputs_same_variant() -> None:
    exp = uuid.uuid4()
    lead = uuid.uuid4()
    variants = [{"id": "a", "weight": 50}, {"id": "b", "weight": 50}]
    first = _hash_pick(experiment_id=exp, lead_id=lead, variants=variants)
    second = _hash_pick(experiment_id=exp, lead_id=lead, variants=variants)
    assert first == second


def test_different_leads_approximate_split() -> None:
    exp = uuid.uuid4()
    variants = [{"id": "a", "weight": 50}, {"id": "b", "weight": 50}]
    picks = Counter(
        _hash_pick(experiment_id=exp, lead_id=uuid.uuid4(), variants=variants)
        for _ in range(2000)
    )
    # Allow 10% skew given 2k samples.
    assert 900 < picks["a"] < 1100
    assert 900 < picks["b"] < 1100


def test_skewed_weights() -> None:
    exp = uuid.uuid4()
    variants = [{"id": "a", "weight": 80}, {"id": "b", "weight": 20}]
    picks = Counter(
        _hash_pick(experiment_id=exp, lead_id=uuid.uuid4(), variants=variants)
        for _ in range(2000)
    )
    assert picks["a"] > picks["b"] * 2.5  # roughly 4x but noisy


def test_zero_weights_fallback() -> None:
    variants = [{"id": "a", "weight": 0}, {"id": "b", "weight": 0}]
    pick = _hash_pick(experiment_id=uuid.uuid4(), lead_id=uuid.uuid4(), variants=variants)
    assert pick == "a"  # first variant when total is zero
