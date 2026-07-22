"""High-level: format + deliver a notification to a Slack incoming webhook.

Best-effort — a notification must never crash the automation run or the cron
sweep that triggered it, so this catches all delivery/transport errors and
returns a bool. The caller decides what to do with ``False`` (today: count/log).
"""

from __future__ import annotations

import httpx

from notifications.formatter import build_slack_payload
from notifications.models import SlackNotification
from notifications.slack_client import SlackClient, SlackDeliveryError
from shared import get_logger

logger = get_logger(__name__)


async def send_slack_notification(
    webhook_url: str,
    notification: SlackNotification,
    *,
    http: httpx.AsyncClient | None = None,
) -> bool:
    """Deliver ``notification`` to ``webhook_url``. Returns True on success.

    Never raises: an empty URL or any delivery failure returns False (logged).
    """
    if not webhook_url:
        return False
    client = SlackClient(http=http)
    try:
        await client.post(webhook_url, build_slack_payload(notification))
        return True
    except SlackDeliveryError as exc:
        logger.warning("slack.delivery_failed", status=exc.status, body=exc.body[:200])
        return False
    except Exception as exc:
        # Best-effort contract: never propagate. Covers httpx transport errors AND
        # non-HTTPError cases like httpx.InvalidURL (a malformed stored webhook),
        # which must not crash the automation run / cron sweep that called us.
        logger.warning("slack.send_failed", error=str(exc))
        return False
    finally:
        await client.close()
