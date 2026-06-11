from integrations.whatsapp.webhook import (
    parse_inbound_payload,
    parse_message_echo_payload,
)


def test_parse_text_message() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "42"},
                            "messages": [
                                {
                                    "id": "abc",
                                    "from": "39333000000",
                                    "type": "text",
                                    "text": {"body": "ciao"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    events = parse_inbound_payload(payload)
    assert len(events) == 1
    assert events[0].text == "ciao"
    assert events[0].phone_number_id == "42"


def test_parse_status_only_returns_empty() -> None:
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}
    assert parse_inbound_payload(payload) == []


def test_parse_phone_echo_text() -> None:
    """Coexistence echo: the merchant typed `ok ci sentiamo` from the phone app."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA-1",
                "changes": [
                    {
                        "field": "smb_message_echoes",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+393331112222",
                                "phone_number_id": "42",
                            },
                            "message_echoes": [
                                {
                                    "from": "393331112222",
                                    "to": "393999000111",
                                    "id": "wamid.ECHO_1",
                                    "timestamp": "1716800000",
                                    "type": "text",
                                    "text": {"body": "ok ci sentiamo"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    echoes = parse_message_echo_payload(payload)
    assert len(echoes) == 1
    e = echoes[0]
    assert e.phone_number_id == "42"
    assert e.business_phone == "393331112222"
    assert e.customer_phone == "393999000111"
    assert e.message_id == "wamid.ECHO_1"
    assert e.text == "ok ci sentiamo"

    # And the *inbound* parser must NOT pick up echo envelopes — otherwise a
    # phone-typed message would be treated as a customer message and fed to
    # the LLM. This is the single most important invariant of the pair.
    assert parse_inbound_payload(payload) == []


def test_parse_inbound_ignores_echo_field_even_with_messages_key() -> None:
    """Defensive: if a buggy upstream put a `messages` array under a
    `smb_message_echoes` change, the inbound parser still skips it.
    """
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "smb_message_echoes",
                        "value": {
                            "metadata": {"phone_number_id": "42"},
                            "messages": [
                                {
                                    "id": "abc",
                                    "from": "39333000000",
                                    "type": "text",
                                    "text": {"body": "ciao"},
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }
    assert parse_inbound_payload(payload) == []


def test_parse_phone_echo_missing_field_returns_empty() -> None:
    """Inbound payloads (no `smb_message_echoes` field) must yield no echoes."""
    inbound = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "42"},
                            "messages": [
                                {
                                    "id": "abc",
                                    "from": "39333000000",
                                    "type": "text",
                                    "text": {"body": "ciao"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }
    assert parse_message_echo_payload(inbound) == []
