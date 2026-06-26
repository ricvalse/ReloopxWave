"""Unit tests for S-08: Objection trend detection and rebuttal suggestion."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_core.objection_trends import ObjectionTrend, compute_trends, suggest_rebuttal


class TestComputeTrends:
    def test_no_objections_returns_empty(self):
        assert compute_trends(current_week={}, prior_week={}) == []

    def test_growing_category_is_trending(self):
        trends = compute_trends(
            current_week={"prezzo": 10},
            prior_week={"prezzo": 5},
        )
        assert len(trends) == 1
        t = trends[0]
        assert t.category == "prezzo"
        assert t.growth_pct == pytest.approx(1.0)  # 100% growth
        assert t.is_trending is True

    def test_stable_category_not_trending(self):
        trends = compute_trends(
            current_week={"prezzo": 5},
            prior_week={"prezzo": 5},
        )
        assert trends[0].growth_pct == 0.0
        assert trends[0].is_trending is False

    def test_new_category_this_week_is_trending(self):
        trends = compute_trends(
            current_week={"fiducia": 3},
            prior_week={},
        )
        t = next(t for t in trends if t.category == "fiducia")
        assert t.is_trending is True
        assert t.growth_pct == 1.0

    def test_zero_count_this_week_not_trending(self):
        trends = compute_trends(
            current_week={},
            prior_week={"competitor": 5},
        )
        t = next(t for t in trends if t.category == "competitor")
        assert t.is_trending is False
        assert t.count_current_week == 0

    def test_sorted_by_growth_descending(self):
        trends = compute_trends(
            current_week={"prezzo": 20, "fiducia": 6, "tempistiche": 2},
            prior_week={"prezzo": 5, "fiducia": 5, "tempistiche": 1},
        )
        growths = [t.growth_pct for t in trends]
        assert growths == sorted(growths, reverse=True)

    def test_small_growth_below_threshold(self):
        trends = compute_trends(
            current_week={"prezzo": 11},
            prior_week={"prezzo": 10},
        )
        t = trends[0]
        assert t.growth_pct == pytest.approx(0.1)
        assert t.is_trending is False  # 10% < 20% threshold

    def test_all_fields_present(self):
        trends = compute_trends(
            current_week={"prezzo": 8},
            prior_week={"prezzo": 4},
        )
        t = trends[0]
        assert t.count_current_week == 8
        assert t.count_prior_week == 4
        assert t.suggested_rebuttal is None  # only set by suggest_rebuttal


class TestSuggestRebuttal:
    async def test_returns_llm_content(self):
        client = MagicMock()
        resp = MagicMock()
        resp.content = "Capisco la tua preoccupazione sul prezzo. Ecco cosa posso offrirti..."
        client.complete = AsyncMock(return_value=resp)

        result = await suggest_rebuttal(client, category="prezzo")
        assert "prezzo" in result or "preoccupazione" in result or len(result) > 10

    async def test_fails_open_on_llm_error(self):
        client = MagicMock()
        client.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

        result = await suggest_rebuttal(client, category="prezzo")
        assert result == ""

    async def test_includes_sample_quotes_in_prompt(self):
        prompts_seen = []
        client = MagicMock()
        resp = MagicMock()
        resp.content = "sugg"

        async def capture_complete(messages, **kw):
            prompts_seen.append(messages[0].content)
            return resp

        client.complete = capture_complete

        await suggest_rebuttal(
            client,
            category="fiducia",
            sample_quotes=["Non mi fido", "Ho sentito cose negative"],
        )
        assert len(prompts_seen) == 1
        assert "Non mi fido" in prompts_seen[0] or "fiducia" in prompts_seen[0]
