"""Relooptech router integration.

Wave Marketing onboards WhatsApp numbers via the platform-wide router (a
360dialog Partner owned by Relooptech). This package wraps the two pieces
of that contract on the platform side:

- `verify_router_signature` — HMAC-SHA256 verification of
  `X-Relooptech-Signature` on inbound webhook and notify calls from the
  router.
- `RouterClient` — server-to-server caller for `POST /onboard/start`,
  which mints a CSRF state token consumed by the 360dialog Embedded
  Signup popup. Signs the request body with the same
  `X-Relooptech-Signature` scheme.

The full contract lives in `RELOOPROUTERSETUP.md` at the repo root.
"""

from integrations.router.client import OnboardStartResult, RouterClient
from integrations.router.signatures import (
    SIGNATURE_HEADER,
    sign_router_payload,
    verify_router_signature,
)

__all__ = [
    "SIGNATURE_HEADER",
    "OnboardStartResult",
    "RouterClient",
    "sign_router_payload",
    "verify_router_signature",
]
