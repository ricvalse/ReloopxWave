"""Outbound notification channels.

Isolated by design: this package has **no** imports from ``db``, ``ai_core`` or
``config_resolver``. It takes primitives (a webhook URL + a plain dataclass) and
talks to the outside world. The glue that reads a merchant's stored webhook and
builds the payload from domain state lives in the core (the automation engine /
scheduler), not here — so removing Slack is ``rm -rf libs/notifications`` plus
reverting a handful of thin call sites.

Depends only on ``httpx`` + ``tenacity`` + ``shared`` (logger), exactly like the
``integrations`` lib. That keeps it portable to another SaaS if the shared-stack
goal ever materialises.
"""

from __future__ import annotations

from notifications.dispatch import send_slack_notification
from notifications.formatter import build_slack_payload
from notifications.models import (
    KIND_HANDOFF,
    KIND_HANDOFF_OVERDUE,
    SlackNotification,
)
from notifications.slack_client import SlackClient, SlackDeliveryError

__all__ = [
    "KIND_HANDOFF",
    "KIND_HANDOFF_OVERDUE",
    "SlackClient",
    "SlackDeliveryError",
    "SlackNotification",
    "build_slack_payload",
    "send_slack_notification",
]
