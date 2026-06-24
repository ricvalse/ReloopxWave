"""UC-09 — the A/B primary metric is constrained to emitted event types.

Free-text primary metrics silently produced zero-conversion experiments (the
metrics endpoint looks the value up in `events_by_type`). The `ExperimentIn`
schema now restricts it to the conversion events the pipeline actually tags with
`variant_id`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.routers.ab_test import ExperimentIn


def _payload(metric: str) -> dict:
    return {
        "name": "exp",
        "variants": [{"id": "A", "weight": 50}, {"id": "B", "weight": 50}],
        "primary_metric": metric,
    }


@pytest.mark.parametrize(
    "metric",
    ["booking.created", "pipeline.moved", "conversation.escalated", "message.replied"],
)
def test_valid_metrics_accepted(metric: str) -> None:
    model = ExperimentIn(**_payload(metric))
    assert model.primary_metric == metric


@pytest.mark.parametrize("metric", ["conversions", "bookings", "", "lead_score_changed"])
def test_invalid_free_text_metric_rejected(metric: str) -> None:
    with pytest.raises(ValidationError):
        ExperimentIn(**_payload(metric))
