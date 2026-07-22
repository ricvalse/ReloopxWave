"""Channel-agnostic notification payload.

Built by the core from domain state, consumed by the formatter. Deliberately
free of any DB/ORM type so this package stays decoupled from the rest of the
monorepo.
"""

from __future__ import annotations

from dataclasses import dataclass

# `kind` discriminator — drives the default header/emoji in the formatter.
KIND_HANDOFF = "handoff"
KIND_HANDOFF_OVERDUE = "handoff_overdue"


@dataclass(slots=True, frozen=True)
class SlackNotification:
    """Everything the Slack formatter needs to render a handoff alert.

    All fields are plain primitives so the core can build one without leaking a
    Conversation/Lead ORM object into this package.
    """

    kind: str
    lead_name: str
    phone: str
    reason: str | None = None
    summary: str | None = None
    last_message: str | None = None
    inbox_url: str | None = None
    # When set (and non-blank), replaces the default Block Kit layout with this
    # single merchant-authored line. Placeholders substituted by the formatter:
    # {name} {phone} {reason} {last_message}.
    custom_text: str | None = None
    # KIND_HANDOFF_OVERDUE only: how long the handoff has been waiting (minutes).
    overdue_minutes: int | None = None
