"""UC-09 — PromptManager applies a variant's authored prompt, else falls back.

This is the behavior that makes A/B real: before this, both arms ran the
identical config-cascade prompt and an experiment could not differentiate.
"""
from __future__ import annotations

from uuid import uuid4

import ai_core.prompt_manager as pm
from ai_core.prompt_manager import PromptManager


class _FakeRepo:
    """Stands in for PromptRepository: only 'treatment' has a saved template."""

    def __init__(self, session) -> None:
        self._session = session

    async def get_active_body(self, *, merchant_id, kind, variant_id):
        assert kind == "system"
        return {"treatment": "VARIANT PROMPT"}.get(variant_id)


async def _fallback() -> str:
    return "CASCADE PROMPT"


async def test_variant_with_template_uses_template(monkeypatch) -> None:
    monkeypatch.setattr(pm, "PromptRepository", _FakeRepo)
    out = await PromptManager(object()).resolve_system_prompt(
        merchant_id=uuid4(), variant_id="treatment", fallback=_fallback
    )
    assert out == "VARIANT PROMPT"


async def test_variant_without_template_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(pm, "PromptRepository", _FakeRepo)
    out = await PromptManager(object()).resolve_system_prompt(
        merchant_id=uuid4(), variant_id="control", fallback=_fallback
    )
    assert out == "CASCADE PROMPT"


async def test_no_variant_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(pm, "PromptRepository", _FakeRepo)
    out = await PromptManager(object()).resolve_system_prompt(
        merchant_id=uuid4(), variant_id=None, fallback=_fallback
    )
    assert out == "CASCADE PROMPT"
