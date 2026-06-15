"""GHL marketplace (INSTALL/UNINSTALL) RSA signature verification.

These events are signed with GHL's RSA public key, not the HMAC used for data
webhooks. The verifier must accept a valid signature, reject a tampered body,
and fail closed on missing key/header or malformed base64.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from integrations.ghl.marketplace_signatures import verify_ghl_marketplace_signature


def _keypair() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return key, pub_pem


def _sign(key: rsa.RSAPrivateKey, payload: bytes) -> str:
    sig = key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


def test_valid_signature_accepted() -> None:
    key, pub = _keypair()
    payload = b'{"type":"INSTALL","locationId":"loc-9"}'
    header = _sign(key, payload)
    assert verify_ghl_marketplace_signature(
        payload=payload, signature_header=header, public_key_pem=pub
    )


def test_tampered_payload_rejected() -> None:
    key, pub = _keypair()
    header = _sign(key, b'{"type":"INSTALL"}')
    assert not verify_ghl_marketplace_signature(
        payload=b'{"type":"UNINSTALL"}', signature_header=header, public_key_pem=pub
    )


def test_wrong_key_rejected() -> None:
    key_a, _ = _keypair()
    _key_b, pub_b = _keypair()
    payload = b'{"type":"INSTALL"}'
    header = _sign(key_a, payload)
    assert not verify_ghl_marketplace_signature(
        payload=payload, signature_header=header, public_key_pem=pub_b
    )


def test_missing_header_or_key_rejected() -> None:
    key, pub = _keypair()
    payload = b'{"type":"INSTALL"}'
    header = _sign(key, payload)
    assert not verify_ghl_marketplace_signature(
        payload=payload, signature_header="", public_key_pem=pub
    )
    assert not verify_ghl_marketplace_signature(
        payload=payload, signature_header=header, public_key_pem=""
    )


def test_malformed_base64_rejected() -> None:
    _key, pub = _keypair()
    assert not verify_ghl_marketplace_signature(
        payload=b"x", signature_header="!!!not-base64!!!", public_key_pem=pub
    )
