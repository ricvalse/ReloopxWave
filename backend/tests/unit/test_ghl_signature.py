"""GHL webhook HMAC-SHA256 signature verification."""

from __future__ import annotations

import hashlib
import hmac

from integrations.ghl.signatures import verify_ghl_signature


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_valid_signature_accepted() -> None:
    secret = "ghl-shared-secret"
    body = b'{"type":"OpportunityUpdate","id":"op_123"}'
    sig = _sign(secret, body)
    assert verify_ghl_signature(shared_secret=secret, payload=body, signature_header=sig)


def test_signature_with_sha256_prefix_accepted() -> None:
    secret = "ghl-shared-secret"
    body = b"{}"
    sig = _sign(secret, body)
    assert verify_ghl_signature(
        shared_secret=secret, payload=body, signature_header=f"sha256={sig}"
    )


def test_wrong_secret_rejected() -> None:
    body = b"{}"
    sig = _sign("other-secret", body)
    assert not verify_ghl_signature(
        shared_secret="right-secret", payload=body, signature_header=sig
    )


def test_tampered_body_rejected() -> None:
    secret = "ghl-shared-secret"
    body = b'{"type":"OpportunityUpdate"}'
    sig = _sign(secret, body)
    tampered = b'{"type":"OpportunityHijack"}'
    assert not verify_ghl_signature(shared_secret=secret, payload=tampered, signature_header=sig)


def test_empty_header_rejected() -> None:
    assert not verify_ghl_signature(shared_secret="s", payload=b"{}", signature_header="")


def test_empty_secret_rejected_even_with_matching_sig() -> None:
    body = b"{}"
    sig = _sign("", body)
    assert not verify_ghl_signature(shared_secret="", payload=body, signature_header=sig)
