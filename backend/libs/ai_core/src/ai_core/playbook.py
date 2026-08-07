"""Conversation playbook runtime (ADR 0018).

The playbook makes the conversation's SHAPE use-case-agnostic: instead of the
sales FSM + scoring + booking machinery running unconditionally, a per-tenant
`conversation.playbook` doc (resolved through the config cascade) plus a few
discrete capability flags decide what runs. The defaults reproduce today's
sales behavior byte-for-byte, so nothing regresses.

`PlaybookRuntime` is the resolved, engine-facing view. It is built once per turn
and shared by the three prompt-assembly paths (inbound conversation, proactive
automation, UC-08 playground) so they gate identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from config_resolver import ConfigKey, ConfigResolver


@dataclass(frozen=True, slots=True)
class PlaybookRuntime:
    # "fsm_legacy" (default) | "off" | "data". "data" is Fase 1 (a data-defined
    # state machine) and is treated as fsm_legacy until the engine consumes it.
    mode: str = "fsm_legacy"
    # Action allowlist; None = all actions allowed (today's behavior).
    allowed_actions: set[str] | None = None
    # Authoritative behavioral rules (goal folded in as the first directive),
    # injected high-salience into the prompt.
    directives: tuple[str, ...] = ()
    scoring_enabled: bool = True
    pipeline_auto_advance: bool = True
    booking_enabled: bool = True
    lead_capture_enabled: bool = True
    # Keyword vocabulary forcing the escalation model route. None = code default.
    critical_keywords: tuple[str, ...] | None = None

    @property
    def fsm_enabled(self) -> bool:
        """Whether the built-in FSM per-turn hints + state transitions run.

        "off" disables them (a pure directive-driven bot). "data" is Fase 1 and
        currently falls back to the legacy FSM.
        """
        return self.mode != "off"


async def resolve_playbook_runtime(
    session: Any, merchant_id: UUID, *, profile_id: UUID | None = None
) -> PlaybookRuntime:
    """Resolve the playbook doc + capability flags for a merchant.

    Best-effort per key: any resolution error degrades that knob to its default
    (today's behavior), never breaking the turn.

    `profile_id` è il livello 0 della cascata (ADR 0022): il playbook è
    precisamente ciò che un profilo modula — obiettivo, direttive, azioni
    permesse, e `mode` per spegnere la FSM di vendita su un profilo non
    commerciale. Con `None` il risultato è identico a prima dei profili.
    """
    resolver = ConfigResolver(session)

    async def _get(key: ConfigKey, default: Any) -> Any:
        try:
            value = await resolver.resolve(key, merchant_id=merchant_id, profile_id=profile_id)
        except Exception:
            return default
        return value if value is not None else default

    async def _bool(key: ConfigKey, default: bool) -> bool:
        value = await _get(key, default)
        return value if isinstance(value, bool) else default

    # Playbook is resolved PER-LEAF (each knob cascades independently) so a
    # merchant can override the mode while inheriting the agency's directives.
    mode = await _get(ConfigKey.CONVERSATION_PLAYBOOK_MODE, "fsm_legacy") or "fsm_legacy"
    goal = str(await _get(ConfigKey.CONVERSATION_PLAYBOOK_GOAL, "") or "").strip()
    raw_directives = await _get(ConfigKey.CONVERSATION_PLAYBOOK_DIRECTIVES, [])
    directive_list = [
        str(d).strip() for d in (raw_directives or []) if str(d).strip()
    ]
    directives = tuple(
        ([f"Obiettivo della conversazione: {goal}"] if goal else []) + directive_list
    )

    enabled = await _get(ConfigKey.CONVERSATION_PLAYBOOK_ACTIONS_ENABLED, None)
    allowed_actions = {str(a) for a in enabled} if isinstance(enabled, list) else None

    raw_keywords = await _get(ConfigKey.HANDOFF_CRITICAL_KEYWORDS, None)
    critical_keywords = (
        tuple(str(k) for k in raw_keywords) if isinstance(raw_keywords, list) else None
    )

    return PlaybookRuntime(
        mode=str(mode),
        allowed_actions=allowed_actions,
        directives=directives,
        scoring_enabled=await _bool(ConfigKey.SCORING_ENABLED, True),
        pipeline_auto_advance=await _bool(ConfigKey.PIPELINE_AUTO_ADVANCE, True),
        booking_enabled=await _bool(ConfigKey.BOOKING_ENABLED, True),
        lead_capture_enabled=await _bool(ConfigKey.LEAD_CAPTURE_ENABLED, True),
        critical_keywords=critical_keywords,
    )
