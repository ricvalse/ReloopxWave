"""OAuth state signing roundtrip + tamper + expiry.

State is what prevents CSRF on the callback, so the three properties below
are the safety contract: a state we didn't sign is rejected, a state past
its TTL is rejected, and a tampered state is rejected.
"""

from __future__ import annotations

import time
import uuid

import pytest

from integrations.ghl.oauth import (
    STATE_TTL_SECONDS,
    sign_oauth_state,
    verify_oauth_state,
)
from shared import IntegrationError


def test_roundtrip_extracts_merchant_id() -> None:
    merchant_id = uuid.uuid4()
    state = sign_oauth_state(merchant_id=merchant_id, secret="s")

    verified = verify_oauth_state(state, secret="s")
    assert verified.merchant_id == merchant_id
    assert verified.expires_at > int(time.time())


def test_different_secret_rejected() -> None:
    state = sign_oauth_state(merchant_id=uuid.uuid4(), secret="issued-with-this")
    with pytest.raises(IntegrationError) as excinfo:
        verify_oauth_state(state, secret="verified-with-that")
    assert excinfo.value.error_code == "oauth_state_invalid"


def test_tampered_payload_rejected() -> None:
    state = sign_oauth_state(merchant_id=uuid.uuid4(), secret="s")
    payload_b64, sig = state.split(".", 1)
    tampered = f"{payload_b64}x.{sig}"
    with pytest.raises(IntegrationError) as excinfo:
        verify_oauth_state(tampered, secret="s")
    assert excinfo.value.error_code == "oauth_state_invalid"


def test_expired_state_rejected() -> None:
    merchant_id = uuid.uuid4()
    # Sign with a fixed "now" in the past so the token is already expired.
    past = int(time.time()) - STATE_TTL_SECONDS - 5
    state = sign_oauth_state(merchant_id=merchant_id, secret="s", now=past)
    with pytest.raises(IntegrationError) as excinfo:
        verify_oauth_state(state, secret="s")
    assert excinfo.value.error_code == "oauth_state_expired"


def test_malformed_state_rejected() -> None:
    with pytest.raises(IntegrationError) as excinfo:
        verify_oauth_state("no-dot-here", secret="s")
    assert excinfo.value.error_code == "oauth_state_malformed"


def test_missing_secret_raises() -> None:
    with pytest.raises(IntegrationError) as excinfo:
        sign_oauth_state(merchant_id=uuid.uuid4(), secret="")
    assert excinfo.value.error_code == "oauth_state_secret_missing"
