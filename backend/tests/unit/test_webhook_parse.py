from integrations.whatsapp.webhook import parse_inbound_payload


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
