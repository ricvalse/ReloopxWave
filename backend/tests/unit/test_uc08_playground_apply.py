"""UC-08 — POST /playground/apply: merge tester rules into the config override.

These pin the pure merge logic that folds the playground's ad-hoc rules into the
merchant's `bot.system_prompt_additions` override (the live flow then picks them
up through the same cascade the playground previews).
"""

from __future__ import annotations

from api.routers.playground import _RULES_HEADER, _merge_rules_into_additions


def test_merge_creates_block_when_no_existing_additions() -> None:
    out = _merge_rules_into_additions(None, ["Non offrire sconti", "Sii conciso"])
    assert out is not None
    assert out.startswith(_RULES_HEADER)
    assert "- Non offrire sconti" in out
    assert "- Sii conciso" in out


def test_merge_keeps_merchant_prose_and_appends_block() -> None:
    out = _merge_rules_into_additions("Usa un tono formale.", ["Sii conciso"])
    assert out is not None
    assert out.startswith("Usa un tono formale.")
    assert _RULES_HEADER in out
    assert "- Sii conciso" in out


def test_merge_replaces_prior_rules_block_not_duplicates() -> None:
    existing = "Tono formale.\n\n" + _RULES_HEADER + "\n- Vecchia regola"
    out = _merge_rules_into_additions(existing, ["Nuova regola"])
    assert out is not None
    # Prior prose survives, old rules block is replaced (no duplicate header).
    assert out.startswith("Tono formale.")
    assert out.count(_RULES_HEADER) == 1
    assert "- Vecchia regola" not in out
    assert "- Nuova regola" in out


def test_merge_empty_rules_drops_block_but_keeps_prose() -> None:
    existing = "Tono formale.\n\n" + _RULES_HEADER + "\n- Vecchia regola"
    out = _merge_rules_into_additions(existing, [])
    assert out == "Tono formale."


def test_merge_empty_everything_returns_none() -> None:
    assert _merge_rules_into_additions(None, []) is None
    assert _merge_rules_into_additions("   ", ["  "]) is None


def test_merge_blank_rules_are_dropped() -> None:
    out = _merge_rules_into_additions(None, ["  ", "", "Valida"])
    assert out is not None
    assert "- Valida" in out
    assert out.count("- ") == 1
