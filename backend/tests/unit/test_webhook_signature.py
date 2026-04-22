import hashlib
import hmac

from integrations.whatsapp.webhook import parse_inbound_payload, verify_whatsapp_signature


def test_verify_valid_signature() -> None:
    secret = "my-app-secret"
    body = b'{"entry":[]}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_whatsapp_signature(app_secret=secret, payload=body, signature_header=sig)


def test_verify_bad_signature() -> None:
    assert not verify_whatsapp_signature(
        app_secret="secret", payload=b"{}", signature_header="sha256=deadbeef"
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
                                {"id": "abc", "from": "39333000000", "type": "text", "text": {"body": "ciao"}}
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
