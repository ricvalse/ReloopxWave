"""GoHighLevel marketplace webhook signature verification (INSTALL/UNINSTALL).

Marketplace lifecycle events sent to the app's *Default Webhook URL* are signed
with an RSA signature using GHL's published public key — distinct from the
HMAC-SHA256 used for per-location data webhooks (see `signatures.py`). The
signature is a base64-encoded RSA-PKCS1v15/SHA-256 over the raw request body.

The public key (PEM) is provided via config (`ghl_marketplace_public_key`) so it
can be rotated without a code change. Missing key/header → False (drop the
event) rather than raising.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey


def verify_ghl_marketplace_signature(
    *, payload: bytes, signature_header: str, public_key_pem: str
) -> bool:
    """Return True iff `signature_header` is a valid RSA signature of `payload`.

    Missing key/header, malformed base64, non-RSA key, or signature mismatch all
    return False so the caller can reject the event uniformly.
    """
    if not public_key_pem or not signature_header:
        return False
    try:
        signature = base64.b64decode(signature_header.strip(), validate=True)
    except (binascii.Error, ValueError):
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
