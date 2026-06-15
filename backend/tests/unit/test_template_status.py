"""Unit tests for template status parsing + mapping (pure functions)."""

from integrations.whatsapp.d360_templates import map_meta_status_to_local
from integrations.whatsapp.webhook import parse_template_status_payload


def test_map_meta_status_to_local() -> None:
    assert map_meta_status_to_local("APPROVED") == "approved"
    assert map_meta_status_to_local("REJECTED") == "rejected"
    assert map_meta_status_to_local("DISABLED") == "rejected"
    assert map_meta_status_to_local("PENDING") == "pending_approval"
    assert map_meta_status_to_local("PAUSED") == "pending_approval"
    assert map_meta_status_to_local(None) == "pending_approval"


def test_parse_template_status_event() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "message_template_status_update",
                        "value": {
                            "message_template_id": "123",
                            "message_template_name": "reloop_reactivation_ab12",
                            "message_template_language": "it",
                            "event": "APPROVED",
                        },
                    }
                ]
            }
        ]
    }
    events = parse_template_status_payload(payload)
    assert len(events) == 1
    assert events[0].template_name == "reloop_reactivation_ab12"
    assert events[0].event == "APPROVED"
    assert events[0].template_id == "123"


def test_parse_template_status_ignores_other_fields() -> None:
    payload = {"entry": [{"changes": [{"field": "messages", "value": {"messages": []}}]}]}
    assert parse_template_status_payload(payload) == []
