"""Playground response-fix loop (UC-08): scoring, matching, prompt injection.

Covers the pure word-overlap scorer, the top-N matcher (with a fake repo), and
the end-to-end injection into `build_cascade_system_prompt` — the merchant's
correction must surface as a mandatory-override block only when the current
message is relevant, scoped to that merchant.
"""

from __future__ import annotations

import uuid

import pytest

from ai_core import corrections as corr
from ai_core.conversation_service import (
    DEFAULT_SYSTEM_PROMPT,
    build_cascade_system_prompt,
)
from ai_core.corrections import build_correction_lines, score_correction
from config_resolver import ConfigKey

_OVERRIDE_HEADER = "CORREZIONE OBBLIGATORIA"


# ---- scoring -------------------------------------------------------------


def test_score_exact_substring_is_one() -> None:
    assert score_correction("scarpe blu", "avete delle scarpe blu?") == 1.0


def test_score_partial_overlap() -> None:
    # trigger tokens (>2 chars): avete, scarpe, blu, taglia -> 2 of 4 overlap
    score = score_correction(
        "avete scarpe blu in taglia M", "come faccio per le scarpe blu"
    )
    assert score == pytest.approx(0.5)


def test_score_no_overlap_is_zero() -> None:
    assert score_correction("orari di apertura", "vorrei un rimborso") == 0.0


def test_score_empty_is_zero() -> None:
    assert score_correction("", "qualcosa") == 0.0
    assert score_correction("qualcosa", "") == 0.0


# ---- matcher (fake repo) -------------------------------------------------


class _FakeCorrection:
    def __init__(self, trigger: str, original: str, corrected: str, is_active: bool = True):
        self.trigger_message = trigger
        self.original_response = original
        self.corrected_response = corrected
        self.is_active = is_active


def _fake_repo_cls(rows: list[_FakeCorrection]):
    class _R:
        def __init__(self, session) -> None:
            pass

        async def list_for_merchant(self, merchant_id, *, active_only: bool = False):
            return [r for r in rows if (r.is_active or not active_only)]

    return _R


async def test_build_correction_lines_matches_and_caps(monkeypatch) -> None:
    rows = [
        _FakeCorrection("scarpe blu", "no, solo rosse", "sì, blu 36-42"),
        _FakeCorrection("spedizione", "non lo so", "spedizione gratuita sopra 49€"),
        _FakeCorrection("orari", "boh", "lun-ven 9-18"),  # irrelevant
    ]
    monkeypatch.setattr(corr, "BotCorrectionRepository", _fake_repo_cls(rows))

    lines = await build_correction_lines(
        object(), uuid.uuid4(), "avete scarpe blu e info spedizione?", max_matches=2
    )
    assert len(lines) == 2
    joined = "\n".join(lines)
    assert "sì, blu 36-42" in joined
    assert "spedizione gratuita sopra 49€" in joined
    assert "lun-ven 9-18" not in joined  # below relevance floor
    assert joined.count(_OVERRIDE_HEADER) == 2


async def test_build_correction_lines_empty_message(monkeypatch) -> None:
    monkeypatch.setattr(corr, "BotCorrectionRepository", _fake_repo_cls([]))
    assert await build_correction_lines(object(), uuid.uuid4(), None) == []
    assert await build_correction_lines(object(), uuid.uuid4(), "   ") == []


async def test_build_correction_lines_inactive_skipped(monkeypatch) -> None:
    rows = [_FakeCorrection("scarpe blu", "no", "sì blu", is_active=False)]
    monkeypatch.setattr(corr, "BotCorrectionRepository", _fake_repo_cls(rows))
    assert await build_correction_lines(object(), uuid.uuid4(), "scarpe blu") == []


# ---- end-to-end injection into the system prompt -------------------------


def _resolver_cls(values: dict):
    class _R:
        def __init__(self, session, redis=None) -> None:
            pass

        async def resolve(self, key, *, merchant_id):
            return values.get(key)

    return _R


async def _build_prompt(monkeypatch, values, rows, customer_message):
    from ai_core import conversation_service as cs

    monkeypatch.setattr(cs, "ConfigResolver", _resolver_cls(values))
    monkeypatch.setattr(corr, "BotCorrectionRepository", _fake_repo_cls(rows))
    return await build_cascade_system_prompt(
        session=object(),
        merchant_id=uuid.uuid4(),
        customer_message=customer_message,
    )


async def test_matching_correction_injected_as_override(monkeypatch) -> None:
    rows = [_FakeCorrection("scarpe blu", "no, solo rosse", "sì, blu 36-42")]
    prompt = await _build_prompt(
        monkeypatch,
        {ConfigKey.BUSINESS_NAME: "Studio Rossi"},
        rows,
        "avete scarpe blu?",
    )
    assert _OVERRIDE_HEADER in prompt
    assert "sì, blu 36-42" in prompt


async def test_non_matching_correction_not_injected(monkeypatch) -> None:
    rows = [_FakeCorrection("scarpe blu", "no", "sì blu")]
    prompt = await _build_prompt(
        monkeypatch,
        {ConfigKey.BUSINESS_NAME: "Studio Rossi"},
        rows,
        "a che ora aprite domani?",
    )
    assert _OVERRIDE_HEADER not in prompt


async def test_correction_alone_triggers_assembled_prompt(monkeypatch) -> None:
    """A merchant with no profile but a matching correction still gets the
    assembled prompt (not the generic default), with the override appended."""
    rows = [_FakeCorrection("rimborso", "non si può", "rimborso entro 14 giorni")]
    prompt = await _build_prompt(monkeypatch, {}, rows, "vorrei un rimborso")
    assert prompt != DEFAULT_SYSTEM_PROMPT
    assert "rimborso entro 14 giorni" in prompt
