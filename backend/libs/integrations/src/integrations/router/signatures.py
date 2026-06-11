"""HMAC-SHA256 signing + verification for router ↔ platform calls.

Same scheme in both directions:

  - Router → platform (inbound webhook, onboarding notify): the router signs
    the raw response body with the platform's `shared_secret` and sends
    `X-Relooptech-Signature: sha256=<hex>`. We `verify_router_signature`
    against the raw bytes before parsing JSON — re-serializing would
    change byte ordering / whitespace and break the digest.
  - Platform → router (`POST /onboard/start`): we sign the request body
    we're about to send with the same `shared_secret` via
    `sign_router_payload` and put the digest on the same header. The
    router verifies it before minting state.

Reject 401 on missing/mismatched header **before** touching JSON, so a
malformed body never even gets parsed.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Relooptech-Signature"
_PREFIX = "sha256="


def verify_router_signature(
    *, raw_body: bytes, header_value: str | None, shared_secret: str
) -> bool:
    if not header_value or not shared_secret:
        return False
    bare = header_value.removeprefix(_PREFIX)
    expected = hmac.new(shared_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if len(bare) != len(expected):
        return False
    return hmac.compare_digest(bare, expected)


def sign_router_payload(*, raw_body: bytes, shared_secret: str) -> str:
    """Compute the `X-Relooptech-Signature` value for `raw_body`."""
    digest = hmac.new(shared_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"
