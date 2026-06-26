"""Unit tests for S-06: Thompson Sampling A/B bandit."""

from __future__ import annotations

import pytest

from ai_core.bandit.thompson import thompson_sample


class TestThompsonSample:
    def _variants(self, ids: list[str]) -> list[dict]:
        return [{"id": vid} for vid in ids]

    def test_returns_valid_variant(self):
        variants = self._variants(["A", "B"])
        result = thompson_sample(variants, variant_wins={}, variant_totals={})
        assert result in ("A", "B")

    def test_exploits_clear_winner_eventually(self):
        """With overwhelming evidence, the winner should dominate."""
        variants = self._variants(["A", "B"])
        wins = {"A": 0, "B": 100}
        totals = {"A": 100, "B": 100}
        counts = {"A": 0, "B": 0}
        for _ in range(200):
            v = thompson_sample(variants, variant_wins=wins, variant_totals=totals)
            counts[v] += 1
        assert counts["B"] > counts["A"], "B should dominate with 100% conversion vs 0%"

    def test_explores_when_no_data(self):
        """With no data, both variants should be selected at least once in 100 draws."""
        variants = self._variants(["A", "B"])
        seen: set[str] = set()
        for _ in range(100):
            seen.add(thompson_sample(variants, variant_wins={}, variant_totals={}))
        assert "A" in seen and "B" in seen, "Should explore both variants"

    def test_three_variants(self):
        variants = self._variants(["A", "B", "C"])
        result = thompson_sample(
            variants,
            variant_wins={"A": 5, "B": 50, "C": 1},
            variant_totals={"A": 10, "B": 50, "C": 10},
        )
        assert result in ("A", "B", "C")

    def test_empty_variants_returns_default(self):
        result = thompson_sample([], variant_wins={}, variant_totals={})
        assert result == "default"

    def test_single_variant_always_selected(self):
        variants = self._variants(["only"])
        for _ in range(20):
            assert thompson_sample(variants, variant_wins={}, variant_totals={}) == "only"

    def test_unknown_variant_wins_treated_as_zero(self):
        variants = self._variants(["X", "Y"])
        result = thompson_sample(
            variants,
            variant_wins={},
            variant_totals={"X": 10},
        )
        assert result in ("X", "Y")
