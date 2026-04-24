import pytest

from shared.crypto import decrypt_secret, encrypt_secret, generate_kek_base64


def test_round_trip_ascii() -> None:
    kek = generate_kek_base64()
    blob = encrypt_secret("hello-world", kek_base64=kek)
    assert decrypt_secret(blob, kek_base64=kek) == "hello-world"


def test_round_trip_unicode() -> None:
    kek = generate_kek_base64()
    plaintext = "ciao, mondo — ñaño"
    blob = encrypt_secret(plaintext, kek_base64=kek)
    assert decrypt_secret(blob, kek_base64=kek) == plaintext


def test_aad_mismatch_fails() -> None:
    from cryptography.exceptions import InvalidTag

    kek = generate_kek_base64()
    blob = encrypt_secret("x", kek_base64=kek, aad=b"tenant-A")
    mutated = type(blob)(ciphertext=blob.ciphertext, nonce=blob.nonce, aad=b"tenant-B")
    with pytest.raises(InvalidTag):
        decrypt_secret(mutated, kek_base64=kek)


def test_wrong_kek_fails() -> None:
    from cryptography.exceptions import InvalidTag

    blob = encrypt_secret("x", kek_base64=generate_kek_base64())
    with pytest.raises(InvalidTag):
        decrypt_secret(blob, kek_base64=generate_kek_base64())


def test_short_kek_rejected() -> None:
    with pytest.raises(ValueError):
        encrypt_secret("x", kek_base64="dGVzdA==")  # 4 bytes


def test_missing_kek_rejected() -> None:
    with pytest.raises(ValueError):
        encrypt_secret("x", kek_base64="")
