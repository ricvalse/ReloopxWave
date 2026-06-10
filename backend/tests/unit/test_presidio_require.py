"""Presidio NER is contractually mandatory in production (Art. 5.2).

The spaCy model is never present in the unit env, so `build_presidio_transform`
either hits the ImportError path or the model-probe failure — both must degrade
to None when optional, and raise when required.
"""

from __future__ import annotations

import pytest

from ai_core.ft.presidio import build_presidio_transform
from shared import DomainError


def test_optional_degrades_to_none_when_unavailable() -> None:
    assert build_presidio_transform(require=False) is None


def test_required_raises_when_unavailable() -> None:
    with pytest.raises(DomainError):
        build_presidio_transform(require=True)
