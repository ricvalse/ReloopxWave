"""Unit tests UC-10 (bot default): strip dei locked_keys sugli overrides
merchant + delete template via repository (stub DB).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from api.routers.bot_config import _dotted_delete, _strip_locked_keys
from db.repositories.template import BotTemplateRepository


def test_strip_locked_keys_removes_dotted_paths() -> None:
    overrides = {
        "bot": {"tone": "formale", "name": "Amalia"},
        "scoring": {"enabled": True},
    }
    _strip_locked_keys(overrides, {"bot.tone", "scoring.enabled"})

    # Le chiavi locked spariscono, le altre restano intatte.
    assert overrides == {"bot": {"name": "Amalia"}, "scoring": {}}


def test_strip_locked_keys_ignores_missing_and_partial_paths() -> None:
    overrides = {"bot": {"name": "Amalia"}}
    # Path inesistente / ramo mancante: no-op, nessuna eccezione.
    _strip_locked_keys(overrides, {"bot.tone", "scoring.enabled", "missing.deep.key"})
    assert overrides == {"bot": {"name": "Amalia"}}


def test_dotted_delete_does_not_touch_non_dict_branch() -> None:
    bag: dict = {"bot": "scalar"}
    _dotted_delete(bag, "bot.tone")
    assert bag == {"bot": "scalar"}


@pytest.mark.asyncio
async def test_repository_delete_returns_true_when_found() -> None:
    session = MagicMock()
    tmpl = object()
    session.get = AsyncMock(return_value=tmpl)
    session.delete = AsyncMock()
    session.flush = AsyncMock()

    repo = BotTemplateRepository(session)
    assert await repo.delete(uuid4()) is True
    session.delete.assert_awaited_once_with(tmpl)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_repository_delete_returns_false_when_missing() -> None:
    session = MagicMock()
    session.get = AsyncMock(return_value=None)
    session.delete = AsyncMock()
    session.flush = AsyncMock()

    repo = BotTemplateRepository(session)
    assert await repo.delete(uuid4()) is False
    session.delete.assert_not_awaited()
