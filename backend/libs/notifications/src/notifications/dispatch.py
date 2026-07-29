"""High-level: format + deliver a notification to a Slack incoming webhook.

Best-effort — a notification must never crash the automation run or the cron
sweep that triggered it, so this catches all delivery/transport errors and
returns a bool. The caller decides what to do with ``False`` (today: count/log).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

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
    on_permanent_failure: Callable[[int, str], Awaitable[None]] | None = None,
) -> bool:
    """Deliver ``notification`` to ``webhook_url``. Returns True on success.

    Never raises: an empty URL or any delivery failure returns False (logged).

    ``on_permanent_failure`` is awaited with ``(status, body)`` when Slack
    rejects the webhook for good — an archived channel or an uninstalled app
    answers 4xx forever, and the caller is the one that can record the
    integration as broken instead of retrying into the void every handoff. Its
    own failures are swallowed: bookkeeping must not turn a lost notification
    into a crashed automation run.
    """
    if not webhook_url:
        return False
    client = SlackClient(http=http)
    try:
        await client.post(webhook_url, build_slack_payload(notification))
        return True
    except SlackDeliveryError as exc:
        logger.warning("slack.delivery_failed", status=exc.status, body=exc.body[:200])
        if on_permanent_failure is not None and 400 <= exc.status < 500:
            try:
                await on_permanent_failure(exc.status, exc.body[:200])
            except Exception as cb_exc:
                logger.warning("slack.failure_callback_failed", error=str(cb_exc))
        return False
    except Exception as exc:
        # Best-effort contract: never propagate. Covers httpx transport errors AND
        # non-HTTPError cases like httpx.InvalidURL (a malformed stored webhook),
        # which must not crash the automation run / cron sweep that called us.
        logger.warning("slack.send_failed", error=str(exc))
        return False
    finally:
        await client.close()
