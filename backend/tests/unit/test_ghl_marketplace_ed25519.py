"""GHL marketplace webhook verification — Ed25519 (x-ghl-signature) + orchestrator.

GHL migrated marketplace webhook signing to Ed25519 (header `x-ghl-signature`);
the RSA `x-wh-signature` scheme is deprecated 2026-07-01. The orchestrator must
prefer Ed25519 and NOT fall back to RSA when an `x-ghl-signature` is present
(downgrade protection), accept RSA when only `x-wh-signature` is present, treat
the `N/A` placeholder as absent, and fail closed otherwise. The shipped public
keys are global published constants, guarded here against transcription errors.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from integrations.ghl.marketplace_signatures import (
    verify_ghl_marketplace_ed25519_signature,
    verify_ghl_marketplace_webhook,
)
from shared.settings import (
    _GHL_MARKETPLACE_ED25519_PUBKEY_PEM,
    _GHL_MARKETPLACE_RSA_PUBKEY_PEM,
)


def _ed25519_keypair() -> tuple[ed25519.Ed25519PrivateKey, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    pub_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return key, pub_pem


def _ed25519_sign(key: ed25519.Ed25519PrivateKey, payload: bytes) -> str:
    return base64.b64encode(key.sign(payload)).decode("ascii")


def _rsa_keypair() -> tuple[rsa.RSAPrivateKey, str]:
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


def _rsa_sign(key: rsa.RSAPrivateKey, payload: bytes) -> str:
    sig = key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


# --- Ed25519 primitive -----------------------------------------------------


def test_ed25519_valid_signature_accepted() -> None:
    key, pub = _ed25519_keypair()
    payload = b'{"type":"INSTALL","locationId":"loc-9"}'
    header = _ed25519_sign(key, payload)
    assert verify_ghl_marketplace_ed25519_signature(
        payload=payload, signature_header=header, public_key_pem=pub
    )


def test_ed25519_tampered_payload_rejected() -> None:
    key, pub = _ed25519_keypair()
    header = _ed25519_sign(key, b'{"type":"INSTALL"}')
    assert not verify_ghl_marketplace_ed25519_signature(
        payload=b'{"type":"UNINSTALL"}', signature_header=header, public_key_pem=pub
    )


def test_ed25519_wrong_key_rejected() -> None:
    key_a, _ = _ed25519_keypair()
    _key_b, pub_b = _ed25519_keypair()
    payload = b'{"type":"INSTALL"}'
    header = _ed25519_sign(key_a, payload)
    assert not verify_ghl_marketplace_ed25519_signature(
        payload=payload, signature_header=header, public_key_pem=pub_b
    )


def test_ed25519_rejects_rsa_key() -> None:
    # An RSA PEM handed to the Ed25519 verifier must be rejected (type guard).
    key, _ = _ed25519_keypair()
    _rsa_key, rsa_pub = _rsa_keypair()
    payload = b'{"type":"INSTALL"}'
    header = _ed25519_sign(key, payload)
    assert not verify_ghl_marketplace_ed25519_signature(
        payload=payload, signature_header=header, public_key_pem=rsa_pub
    )


def test_ed25519_missing_header_or_key_rejected() -> None:
    key, pub = _ed25519_keypair()
    payload = b'{"type":"INSTALL"}'
    header = _ed25519_sign(key, payload)
    assert not verify_ghl_marketplace_ed25519_signature(
        payload=payload, signature_header="", public_key_pem=pub
    )
    assert not verify_ghl_marketplace_ed25519_signature(
        payload=payload, signature_header=header, public_key_pem=""
    )


def test_ed25519_malformed_base64_rejected() -> None:
    _key, pub = _ed25519_keypair()
    assert not verify_ghl_marketplace_ed25519_signature(
        payload=b"x", signature_header="!!!not-base64!!!", public_key_pem=pub
    )


# --- Orchestrator / precedence --------------------------------------------


def test_orchestrator_prefers_ed25519() -> None:
    ed_key, ed_pub = _ed25519_keypair()
    _rsa_key, rsa_pub = _rsa_keypair()
    payload = b'{"type":"INSTALL","locationId":"loc-1"}'
    assert verify_ghl_marketplace_webhook(
        payload=payload,
        ed25519_signature=_ed25519_sign(ed_key, payload),
        rsa_signature="",
        ed25519_public_key_pem=ed_pub,
        rsa_public_key_pem=rsa_pub,
    )


def test_orchestrator_no_downgrade_to_rsa() -> None:
    # x-ghl-signature present but INVALID, alongside a VALID x-wh-signature.
    # Must reject — no fallback to RSA (downgrade protection).
    ed_key, ed_pub = _ed25519_keypair()
    rsa_key, rsa_pub = _rsa_keypair()
    payload = b'{"type":"INSTALL","locationId":"loc-1"}'
    bad_ed = _ed25519_sign(ed_key, b"different-bytes")
    good_rsa = _rsa_sign(rsa_key, payload)
    assert not verify_ghl_marketplace_webhook(
        payload=payload,
        ed25519_signature=bad_ed,
        rsa_signature=good_rsa,
        ed25519_public_key_pem=ed_pub,
        rsa_public_key_pem=rsa_pub,
    )


def test_orchestrator_rsa_when_only_legacy_present() -> None:
    _ed_key, ed_pub = _ed25519_keypair()
    rsa_key, rsa_pub = _rsa_keypair()
    payload = b'{"type":"UNINSTALL","locationId":"loc-2"}'
    assert verify_ghl_marketplace_webhook(
        payload=payload,
        ed25519_signature="",
        rsa_signature=_rsa_sign(rsa_key, payload),
        ed25519_public_key_pem=ed_pub,
        rsa_public_key_pem=rsa_pub,
    )


def test_orchestrator_na_header_treated_as_absent() -> None:
    # GHL sends "N/A" on a non-applicable header — falls through to RSA.
    _ed_key, ed_pub = _ed25519_keypair()
    rsa_key, rsa_pub = _rsa_keypair()
    payload = b'{"type":"INSTALL","locationId":"loc-3"}'
    assert verify_ghl_marketplace_webhook(
        payload=payload,
        ed25519_signature="N/A",
        rsa_signature=_rsa_sign(rsa_key, payload),
        ed25519_public_key_pem=ed_pub,
        rsa_public_key_pem=rsa_pub,
    )


def test_orchestrator_rejects_when_no_signature() -> None:
    _ed_key, ed_pub = _ed25519_keypair()
    _rsa_key, rsa_pub = _rsa_keypair()
    assert not verify_ghl_marketplace_webhook(
        payload=b"{}",
        ed25519_signature="",
        rsa_signature="",
        ed25519_public_key_pem=ed_pub,
        rsa_public_key_pem=rsa_pub,
    )


# --- Shipped default keys (guard against transcription errors) -------------


def test_shipped_rsa_default_key_is_canonical() -> None:
    key = serialization.load_pem_public_key(_GHL_MARKETPLACE_RSA_PUBKEY_PEM.encode("utf-8"))
    assert isinstance(key, RSAPublicKey)
    der = key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert (
        hashlib.sha256(der).hexdigest()
        == "2f62045d413b1b7747e26770ce7f57840cbf86288cd20ceb90d299a62501fd25"
    )


def test_shipped_ed25519_default_key_loads() -> None:
    key = serialization.load_pem_public_key(_GHL_MARKETPLACE_ED25519_PUBKEY_PEM.encode("utf-8"))
    assert isinstance(key, Ed25519PublicKey)
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert raw.hex() == "8b61d1d6cacbe28d7c3bc0516bb815258ec6edbba96cddc7f40c09ac708388e8"
