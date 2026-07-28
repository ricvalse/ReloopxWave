"""Playbook / use-case-agnostic gating (ADR 0018).

Two invariants under test:
  1. ZERO REGRESSION — with the default (sales) config, every gated seam
     produces byte-identical output to before the change.
  2. GATING — when the playbook disables a capability, the corresponding prompt
     fragment / action disappears (not just gets overridden).
"""

from __future__ import annotations

import uuid

import pytest

from ai_core.conversation_service import (
    DEFAULT_SYSTEM_PROMPT,
    _default_system_prompt,
)
from ai_core.orchestrator import (
    _RESPONSE_SCHEMA_HINT,
    ConversationContext,
    ConversationOrchestrator,
    _combine_allowlists,
    _has_critical_objection,
    render_schema_hint,
)
from ai_core.playbook import PlaybookRuntime, resolve_playbook_runtime
from config_resolver import ConfigKey


# --------------------------------------------------------------------------- #
# render_schema_hint — golden byte-identity + reduction
# --------------------------------------------------------------------------- #
def test_schema_hint_none_is_byte_identical_to_full_constant():
    assert render_schema_hint(None) == _RESPONSE_SCHEMA_HINT


def test_schema_hint_allowlist_omits_forbidden_actions():
    hint = render_schema_hint({"escalate_human", "none"})
    for forbidden in (
        "book_slot",
        "move_pipeline",
        "update_score",
        "propose_slots",
        "check_availability",
        "lookup_appointment",
    ):
        assert forbidden not in hint
    assert "escalate_human" in hint
    assert '"none"' in hint
    # tool-use paragraph + booking note are dropped when their actions aren't allowed
    assert "STRUMENTI DI LETTURA" not in hint
    assert "niente false conferme" not in hint
    # enum lists only allowed kinds
    assert '"kind": "escalate_human|none"' in hint


def test_schema_hint_empty_allowlist_falls_back_to_none_only():
    hint = render_schema_hint(set())
    assert '"kind": "none"' in hint
    assert "book_slot" not in hint


def test_schema_hint_booking_subset_keeps_booking_note():
    hint = render_schema_hint({"book_slot", "propose_slots", "none"})
    assert "niente false conferme" in hint
    assert "update_score" not in hint
    # generic multi-action note (not the update_score+book_slot example)
    assert "Puoi emettere più azioni nello stesso turno." in hint


# --------------------------------------------------------------------------- #
# _default_system_prompt — fallback gating
# --------------------------------------------------------------------------- #
def test_default_prompt_full_is_byte_identical():
    assert (
        _default_system_prompt(booking_enabled=True, lead_capture_enabled=True)
        == DEFAULT_SYSTEM_PROMPT
    )


def test_default_prompt_drops_booking_and_capture_clauses():
    p = _default_system_prompt(booking_enabled=False, lead_capture_enabled=False)
    assert "prenotare" not in p
    assert "informazioni critiche" not in p
    assert "Non inventare fatti" in p  # the safety clause always stays


# --------------------------------------------------------------------------- #
# PlaybookRuntime
# --------------------------------------------------------------------------- #
def test_playbook_defaults_preserve_sales_behavior():
    pb = PlaybookRuntime()
    assert pb.mode == "fsm_legacy"
    assert pb.fsm_enabled is True
    assert pb.allowed_actions is None
    assert pb.scoring_enabled and pb.pipeline_auto_advance
    assert pb.booking_enabled and pb.lead_capture_enabled
    assert pb.directives == ()
    assert pb.critical_keywords is None


def test_playbook_mode_off_disables_fsm():
    assert PlaybookRuntime(mode="off").fsm_enabled is False
    # "data" is Fase 1 → treated as legacy for now
    assert PlaybookRuntime(mode="data").fsm_enabled is True


def test_combine_allowlists():
    assert _combine_allowlists(None, None) is None
    assert _combine_allowlists({"a", "b"}, None) == {"a", "b"}
    assert _combine_allowlists(None, {"a"}) == {"a"}
    assert _combine_allowlists({"a", "b"}, {"b", "c"}) == {"b"}


def test_has_critical_objection_custom_keywords():
    # default vocabulary flags "concorrenza"
    assert _has_critical_objection("parliamo della concorrenza") is True
    # an empty custom vocabulary disables keyword-forced escalation
    assert _has_critical_objection("parliamo della concorrenza", ()) is False
    assert _has_critical_objection("voglio un rimborso", ("rimborso",)) is True


# --------------------------------------------------------------------------- #
# _build_messages — the shared assembler honours the caps on ctx
# --------------------------------------------------------------------------- #
def _ctx(**overrides):
    base = {
        "merchant_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "lead_id": None,
        "lead_score": 0,
        "hot_threshold": 80,
        "system_prompt": "PERSONA",
    }
    base.update(overrides)
    return ConversationContext(**base)


def _system_text(ctx) -> str:
    orch = ConversationOrchestrator(router=None)  # _build_messages never touches the router
    msgs = orch._build_messages(ctx, "ciao")
    return msgs[0].content


def test_build_messages_default_matches_pre_change_shape():
    text = _system_text(_ctx())
    assert _RESPONSE_SCHEMA_HINT in text
    assert "Stato qualificazione del lead" in text  # scoring context present
    assert "REGOLE DELLA CONVERSAZIONE" not in text  # no directives by default


def test_build_messages_scoring_off_drops_qualification_block():
    text = _system_text(_ctx(scoring_enabled=False))
    assert "Stato qualificazione del lead" not in text


def test_build_messages_injects_directives_authoritatively():
    text = _system_text(_ctx(directives=("Mai intervistare il candidato.",)))
    assert "REGOLE DELLA CONVERSAZIONE" in text
    assert "Mai intervistare il candidato." in text


def test_build_messages_reduced_hint_when_actions_restricted():
    # Realistic recruiting caps: scoring off (drops the qualification block that
    # references move_pipeline) AND a restricted action allowlist.
    text = _system_text(_ctx(allowed_actions={"escalate_human", "none"}, scoring_enabled=False))
    assert "book_slot" not in text
    assert "move_pipeline" not in text
    assert "update_score" not in text
    assert "Stato qualificazione del lead" not in text


# --------------------------------------------------------------------------- #
# resolve_playbook_runtime — parsing (goal fold, actions.enabled → set)
# --------------------------------------------------------------------------- #
class _FakeResolver:
    def __init__(self, values):
        self._values = values

    async def resolve(self, key, *, merchant_id, profile_id=None):
        return self._values.get(key)


@pytest.mark.asyncio
async def test_resolve_playbook_runtime_parses_recruiting(monkeypatch):
    values = {
        ConfigKey.CONVERSATION_PLAYBOOK_MODE: "off",
        ConfigKey.CONVERSATION_PLAYBOOK_GOAL: "Ricordare il questionario",
        ConfigKey.CONVERSATION_PLAYBOOK_DIRECTIVES: ["Mai intervistare.", "Chiudi presto."],
        ConfigKey.CONVERSATION_PLAYBOOK_ACTIONS_ENABLED: ["escalate_human", "none"],
        ConfigKey.SCORING_ENABLED: False,
        ConfigKey.PIPELINE_AUTO_ADVANCE: False,
        ConfigKey.BOOKING_ENABLED: False,
        ConfigKey.LEAD_CAPTURE_ENABLED: False,
        ConfigKey.ESCALATION_CRITICAL_KEYWORDS: [],
    }
    monkeypatch.setattr("ai_core.playbook.ConfigResolver", lambda session: _FakeResolver(values))
    pb = await resolve_playbook_runtime(object(), uuid.uuid4())
    assert pb.mode == "off" and pb.fsm_enabled is False
    assert pb.allowed_actions == {"escalate_human", "none"}
    assert pb.scoring_enabled is False and pb.pipeline_auto_advance is False
    assert pb.booking_enabled is False and pb.lead_capture_enabled is False
    assert pb.critical_keywords == ()
    # goal folded in as the first directive
    assert pb.directives[0] == "Obiettivo della conversazione: Ricordare il questionario"
    assert "Mai intervistare." in pb.directives


@pytest.mark.asyncio
async def test_resolve_playbook_runtime_defaults(monkeypatch):
    # No overrides → SYSTEM_DEFAULTS shape (sales) resolves through the real
    # default dict passed here explicitly.
    from config_resolver.schema import SYSTEM_DEFAULTS

    values = {k: SYSTEM_DEFAULTS.get(k) for k in SYSTEM_DEFAULTS}
    monkeypatch.setattr("ai_core.playbook.ConfigResolver", lambda session: _FakeResolver(values))
    pb = await resolve_playbook_runtime(object(), uuid.uuid4())
    assert pb.mode == "fsm_legacy" and pb.fsm_enabled is True
    assert pb.allowed_actions is None
    assert pb.scoring_enabled and pb.pipeline_auto_advance
    assert pb.directives == ()
    assert pb.critical_keywords is None
