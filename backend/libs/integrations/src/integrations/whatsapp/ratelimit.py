"""In-process per-channel outbound rate limiting for WhatsApp sends.

360dialog (proxying Meta Cloud API) throttles outbound throughput and will
return 429 — and degrade the number's quality rating — when a channel bursts
(campaigns, multi-bubble replies, retries firing at once). We smooth sends per
channel to a minimum interval and honour `Retry-After` on 429.

State is per worker **process**, keyed by `phone_number_id`. That covers the
single consolidated ARQ worker today; a multi-replica deployment should
graduate this to a Redis token bucket (see the Amalia↔Reloop audit). Pure
asyncio, no external deps, no module-scope clock.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

# phone_number_id -> gate. Created lazily; never evicted (one entry per channel
# the process has ever sent on — negligible footprint).
_CHANNELS: dict[str, _ChannelGate] = {}


class _ChannelGate:
    """Reserves monotonically increasing send slots for one channel."""

    __slots__ = ("lock", "next_allowed")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.next_allowed = 0.0


async def acquire_channel_slot(channel_key: str, min_interval_s: float) -> None:
    """Block until this channel may send again, then reserve the next slot.

    Concurrent callers each reserve a sequential slot under the lock and sleep
    their own delay outside it, so N simultaneous sends go out spaced by
    `min_interval_s` instead of all at once. A non-positive interval or empty
    key disables throttling (no-op).
    """
    if min_interval_s <= 0 or not channel_key:
        return
    gate = _CHANNELS.get(channel_key)
    if gate is None:
        gate = _ChannelGate()
        _CHANNELS[channel_key] = gate
    async with gate.lock:
        now = time.monotonic()
        slot = max(now, gate.next_allowed)
        gate.next_allowed = slot + min_interval_s
    delay = slot - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)


def parse_retry_after_seconds(headers: Any, *, default: float) -> float:
    """Seconds to wait from a `Retry-After` header (delta-seconds or HTTP-date).

    Returns `default` when the header is absent or unparseable. Negative/garbage
    values clamp to `default`.
    """
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return default
    if not raw:
        return default
    raw = str(raw).strip()
    # Delta-seconds form ("120").
    try:
        secs = float(raw)
        return secs if secs >= 0 else default
    except ValueError:
        pass
    # HTTP-date form ("Wed, 21 Oct 2026 07:28:00 GMT").
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return default
    if when is None:
        return default
    delta = (when - datetime.now(UTC)).total_seconds()
    return delta if delta > 0 else default
