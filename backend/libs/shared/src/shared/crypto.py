"""AES-256-GCM helpers for encrypting per-merchant integration secrets.

The KEK (key-encryption key) lives in the env as `INTEGRATIONS_KEK_BASE64`.
Rotation: bump `kek_version` on the integrations row and re-encrypt.

Ciphertext layout on disk: {nonce: 12B, ciphertext_with_tag: N}. We store
nonce and ciphertext in separate columns so neither can be swapped.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_BYTES = 32  # AES-256


@dataclass(slots=True, frozen=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    aad: bytes | None
    kek_version: int = 1


def _load_kek(kek_base64: str) -> bytes:
    if not kek_base64:
        raise ValueError("INTEGRATIONS_KEK_BASE64 is not configured")
    try:
        kek = base64.b64decode(kek_base64, validate=True)
    except Exception as e:
        raise ValueError("KEK is not valid base64") from e
    if len(kek) != KEY_BYTES:
        raise ValueError(f"KEK must decode to {KEY_BYTES} bytes, got {len(kek)}")
    return kek


def encrypt_secret(plaintext: str, *, kek_base64: str, aad: bytes | None = None) -> EncryptedSecret:
    kek = _load_kek(kek_base64)
    nonce = os.urandom(NONCE_BYTES)
    aesgcm = AESGCM(kek)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
    return EncryptedSecret(ciphertext=ciphertext, nonce=nonce, aad=aad)


def decrypt_secret(
    encrypted: EncryptedSecret,
    *,
    kek_base64: str,
) -> str:
    kek = _load_kek(kek_base64)
    aesgcm = AESGCM(kek)
    plaintext = aesgcm.decrypt(encrypted.nonce, encrypted.ciphertext, encrypted.aad)
    return plaintext.decode("utf-8")


def generate_kek_base64() -> str:
    """Helper for `uv run python -c "from shared.crypto import generate_kek_base64; print(generate_kek_base64())"`."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")
