"""GoHighLevel marketplace webhook signature verification (INSTALL/UNINSTALL).

Marketplace lifecycle events sent to the app's *Default Webhook URL* are signed
by GHL with one of two schemes — distinct from the HMAC-SHA256 used for
per-location data webhooks (see `signatures.py`):

- **Ed25519** (current/preferred), header ``x-ghl-signature``. Signature is a
  base64-encoded Ed25519 signature over the raw request body.
- **RSA-SHA256** (legacy), header ``x-wh-signature``. Signature is a
  base64-encoded RSA-PKCS1v15/SHA-256 over the raw request body. **Deprecated by
  GHL on 2026-07-01** — after that date GHL signs only with ``x-ghl-signature``.

Both keys are *global published constants* (same for every Marketplace app), so
they are shipped as config defaults (`ghl_marketplace_public_key_ed25519` /
`ghl_marketplace_public_key`) and can be overridden via env if GHL rotates them.

`verify_ghl_marketplace_webhook` applies GHL's recommended precedence: if an
``x-ghl-signature`` is present, verify Ed25519 and do **not** fall back to RSA
(downgrade protection); otherwise verify the legacy ``x-wh-signature`` RSA; if
neither is present, reject. Missing key/header, malformed base64, wrong key type
or a signature mismatch all return False (drop the event) rather than raising.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


def _present(signature_header: str) -> bool:
    """True if a signature header carries an actual signature.

    GHL sends the literal ``N/A`` on a header that does not apply (e.g. during
    the transition when only one scheme is used), so treat empty/``N/A`` as
    absent.
    """
    stripped = (signature_header or "").strip()
    return bool(stripped) and stripped.upper() != "N/A"


def _decode_signature(signature_header: str) -> bytes | None:
    try:
        return base64.b64decode(signature_header.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None


def verify_ghl_marketplace_signature(
    *, payload: bytes, signature_header: str, public_key_pem: str
) -> bool:
    """Return True iff `signature_header` is a valid RSA signature of `payload`.

    Legacy ``x-wh-signature`` scheme (RSA-PKCS1v15/SHA-256, base64). Missing
    key/header, malformed base64, non-RSA key, or signature mismatch all return
    False so the caller can reject the event uniformly.
    """
    if not public_key_pem or not signature_header:
        return False
    signature = _decode_signature(signature_header)
    if signature is None:
        return False
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    if not isinstance(public_key, RSAPublicKey):
        return False
    try:
        public_key.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        return False
    return True


def verify_ghl_marketplace_ed25519_signature(
    *, payload: bytes, signature_header: str, public_key_pem: str
) -> bool:
    """Return True iff `signature_header` is a valid Ed25519 signature of `payload`.

    Current ``x-ghl-signature`` scheme (Ed25519, base64 over the raw body). Same
    fail-closed contract as the RSA verifier: missing key/header, malformed
    base64, non-Ed25519 key, or signature mismatch all return False.
    """
    if not public_key_pem or not signature_header:
        return False
    signature = _decode_signature(signature_header)
    if signature is None:
        return False
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    if not isinstance(public_key, Ed25519PublicKey):
        return False
    try:
        public_key.verify(signature, payload)
    except InvalidSignature:
        return False
    return True


def verify_ghl_marketplace_webhook(
    *,
    payload: bytes,
    ed25519_signature: str,
    rsa_signature: str,
    ed25519_public_key_pem: str,
    rsa_public_key_pem: str,
) -> bool:
    """Verify a GHL marketplace webhook using GHL's dual-scheme precedence.

    If an ``x-ghl-signature`` (Ed25519) is present, verify it and do NOT fall
    back to the legacy RSA scheme — this prevents a downgrade attack where a
    forged Ed25519 header rides alongside a valid RSA one. If only the legacy
    ``x-wh-signature`` (RSA) is present, verify that. If neither is present,
    reject.
    """
    if _present(ed25519_signature):
        return verify_ghl_marketplace_ed25519_signature(
            payload=payload,
            signature_header=ed25519_signature,
            public_key_pem=ed25519_public_key_pem,
        )
    if _present(rsa_signature):
        return verify_ghl_marketplace_signature(
            payload=payload,
            signature_header=rsa_signature,
            public_key_pem=rsa_public_key_pem,
        )
    return False
