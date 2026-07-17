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


def _inbound_media(msg: dict) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "42"},
                            "messages": [{"id": "m1", "from": "393330000000", **msg}],
                        }
                    }
                ]
            }
        ]
    }


def test_parse_image_with_caption() -> None:
    """A captioned photo: caption becomes the turn text AND the media descriptor
    carries the media id + mime for the worker to download."""
    events = parse_inbound_payload(
        _inbound_media(
            {
                "type": "image",
                "image": {"id": "MID1", "mime_type": "image/jpeg", "caption": "è questo?"},
            }
        )
    )
    assert len(events) == 1
    e = events[0]
    assert e.kind == "image"
    assert e.text == "è questo?"  # caption reaches the LLM
    assert e.caption == "è questo?"
    assert e.media_id == "MID1"
    assert e.media_mime == "image/jpeg"


def test_parse_image_without_caption() -> None:
    """An uncaptioned photo: no turn text (upstream fills the placeholder), but
    the media descriptor is still populated."""
    events = parse_inbound_payload(
        _inbound_media({"type": "image", "image": {"id": "MID2", "mime_type": "image/png"}})
    )
    e = events[0]
    assert e.text is None
    assert e.caption is None
    assert e.media_id == "MID2"
    assert e.media_mime == "image/png"


def test_parse_audio_media() -> None:
    events = parse_inbound_payload(
        _inbound_media({"type": "audio", "audio": {"id": "AUD1", "mime_type": "audio/ogg"}})
    )
    e = events[0]
    assert e.kind == "audio"
    assert e.media_id == "AUD1"
    assert e.text is None  # voice notes carry no caption


def test_parse_document_falls_back_to_filename() -> None:
    events = parse_inbound_payload(
        _inbound_media(
            {
                "type": "document",
                "document": {
                    "id": "DOC1",
                    "mime_type": "application/pdf",
                    "filename": "listino.pdf",
                },
            }
        )
    )
    e = events[0]
    assert e.kind == "document"
    assert e.caption == "listino.pdf"  # filename used when no caption
    assert e.text == "listino.pdf"
    assert e.media_id == "DOC1"


def test_parse_video_and_sticker() -> None:
    vid = parse_inbound_payload(
        _inbound_media({"type": "video", "video": {"id": "VID1", "mime_type": "video/mp4"}})
    )[0]
    assert vid.kind == "video" and vid.media_id == "VID1"
    stk = parse_inbound_payload(
        _inbound_media({"type": "sticker", "sticker": {"id": "STK1", "mime_type": "image/webp"}})
    )[0]
    assert stk.kind == "sticker" and stk.media_id == "STK1"


def test_parse_echo_media() -> None:
    """A photo the merchant sent from their handset (Coexistence echo) carries a
    media descriptor so it lands in the inbox instead of vanishing."""
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "smb_message_echoes",
                        "value": {
                            "metadata": {"phone_number_id": "42"},
                            "message_echoes": [
                                {
                                    "from": "393331112222",
                                    "to": "393999000111",
                                    "id": "wamid.ECHO_IMG",
                                    "type": "image",
                                    "image": {
                                        "id": "EIMG1",
                                        "mime_type": "image/jpeg",
                                        "caption": "ecco",
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }
    echoes = parse_message_echo_payload(payload)
    assert len(echoes) == 1
    e = echoes[0]
    assert e.kind == "image"
    assert e.media_id == "EIMG1"
    assert e.caption == "ecco"


def test_extract_campaign_from_referral() -> None:
    from api.routers.webhooks import _extract_campaign

    assert _extract_campaign({"referral": {"source_id": "AD123", "headline": "Promo"}}) == "AD123"
    assert _extract_campaign({"referral": {"headline": "Promo estiva"}}) == "Promo estiva"
    assert _extract_campaign({}) is None
    assert _extract_campaign({"referral": "not-a-dict"}) is None
    assert _extract_campaign({"referral": {}}) is None
