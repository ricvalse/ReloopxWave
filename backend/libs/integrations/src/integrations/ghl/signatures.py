"""GoHighLevel webhook signature verification.

GHL signs outgoing webhooks with HMAC-SHA256 using the app's shared secret.
Header name and hex encoding mirror the Meta/Stripe convention:
  `x-gohighlevel-signature: <hex>`
"""

from __future__ import annotations

import hashlib
import hmac


def verify_ghl_signature(*, shared_secret: str, payload: bytes, signature_header: str) -> bool:
    """Returns True iff the header equals HMAC-SHA256(secret, raw_body).

    Missing secret or header → False. Drop unsigned events rather than error.
    """
    if not shared_secret or not signature_header:
        return False
    provided = signature_header.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1]
    expected = hmac.new(
        shared_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(provided, expected)
