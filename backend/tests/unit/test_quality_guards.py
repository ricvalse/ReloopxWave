"""Unit tests for S-04: CoherenceGuard and ContextCompressor."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_core.quality.coherence import CoherenceGuard, CoherenceResult
from ai_core.quality.compressor import ContextCompressor, MemoryBlock


class FakeMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


def _make_llm(content: str) -> Any:
    client = MagicMock()
    resp = MagicMock()
    resp.content = content
    client.complete = AsyncMock(return_value=resp)
    return client


class TestCoherenceGuard:
    async def test_coherent_reply_passes(self):
        llm = _make_llm('{"coherent": true, "issue": null}')
        guard = CoherenceGuard(llm)
        history = [FakeMessage("user", "Mi chiamo Luca")]
        result = await guard.check(history, "Ciao Luca, come posso aiutarti?")
        assert result.coherent is True
        assert result.issue is None

    async def test_incoherent_reply_detected(self):
        llm = _make_llm('{"coherent": false, "issue": "Ha chiamato il cliente Mario invece di Luca"}')
        guard = CoherenceGuard(llm)
        history = [FakeMessage("user", "Mi chiamo Luca")]
        result = await guard.check(history, "Ciao Mario!")
        assert result.coherent is False
        assert result.issue is not None

    async def test_fails_open_on_llm_error(self):
        llm = MagicMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        guard = CoherenceGuard(llm)
        result = await guard.check([FakeMessage("user", "test")], "risposta")
        assert result.coherent is True

    async def test_empty_history_skips_check(self):
        llm = _make_llm('{"coherent": false, "issue": "test"}')
        guard = CoherenceGuard(llm)
        result = await guard.check([], "risposta")
        assert result.coherent is True  # no history → skip guard
        llm.complete.assert_not_called()


class TestContextCompressor:
    async def test_compress_returns_memory_block(self):
        llm = _make_llm("Lead: Luca, interessato al prodotto X, budget 1000€")
        compressor = ContextCompressor(llm)
        messages = [FakeMessage("user" if i % 2 == 0 else "assistant", f"msg {i}") for i in range(40)]
        block = await compressor.compress(messages)
        assert block is not None
        assert isinstance(block, MemoryBlock)
        assert block.compressed_turns == 30  # 40 - 10 kept recent
        assert "Lead" in block.text

    async def test_compress_fails_open_on_llm_error(self):
        llm = MagicMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        compressor = ContextCompressor(llm)
        messages = [FakeMessage("user", f"msg {i}") for i in range(40)]
        block = await compressor.compress(messages)
        assert block is None

    async def test_compress_short_history_still_works(self):
        llm = _make_llm("Sommario breve")
        compressor = ContextCompressor(llm)
        messages = [FakeMessage("user", "msg")]
        block = await compressor.compress(messages)
        # Only 1 message, _KEEP_RECENT=10 → no "older" turns → None
        assert block is None
