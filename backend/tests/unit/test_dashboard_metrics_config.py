"""Dashboard configurabile — chiave `dashboard.metrics` del cascade (ADR 0021)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config_resolver.schema import (
    SYSTEM_DEFAULTS,
    BotConfigSchema,
    ConfigKey,
    DashboardConfig,
    MetricDefinitionSchema,
)
from db.analytics_events import EventType

# --- Default di sistema ---------------------------------------------------


def test_system_default_metrics_exist_and_are_valid() -> None:
    raw = SYSTEM_DEFAULTS[ConfigKey.DASHBOARD_METRICS]
    assert raw, "il default di sistema non deve essere vuoto"
    # Devono passare la validazione tipata (event_type ∈ catalogo, id unici).
    parsed = DashboardConfig(metrics=raw)
    assert len(parsed.metrics) == len(raw)


def test_default_dashboard_matches_bot_config_schema_default() -> None:
    """Un merchant senza override vede comunque un set di metriche sensato."""
    schema = BotConfigSchema()
    ids = [m.id for m in schema.dashboard.metrics]
    assert "bookings_created" in ids, "gli appuntamenti presi sono la metrica chiave del cliente"
    assert "pipeline_moved" in ids, "gli spostamenti in pipeline erano l'altro esempio richiesto"


def test_default_reminder_metric_points_at_the_emitted_event() -> None:
    """Regressione del bug ADR 0021: il default NON deve usare `reminder.sent`."""
    raw = SYSTEM_DEFAULTS[ConfigKey.DASHBOARD_METRICS]
    reminder = next(m for m in raw if m["id"] == "reminders_sent")
    assert reminder["event_type"] == EventType.APPOINTMENT_REMINDER_SENT.value


# --- Validazione delle definizioni ---------------------------------------


def test_metric_rejects_event_type_outside_catalog() -> None:
    """Una metrica non può puntare a un evento che il sistema non emette mai."""
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(id="ghost", label="Fantasma", event_type="reminder.sent")
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(id="ghost", label="Fantasma", event_type="totally.made.up")


def test_metric_accepts_every_catalog_event() -> None:
    for event_type in EventType:
        MetricDefinitionSchema(id="m", label="M", event_type=event_type.value)


def test_metric_id_shape_is_enforced() -> None:
    MetricDefinitionSchema(id="ok_id_1", label="L", event_type=EventType.BOOKING_CREATED.value)
    for bad in ("Has Spaces", "UPPER", "trattino-no", ""):
        with pytest.raises(ValidationError):
            MetricDefinitionSchema(id=bad, label="L", event_type=EventType.BOOKING_CREATED.value)


def test_duplicate_metric_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        DashboardConfig(
            metrics=[
                {"id": "dup", "label": "A", "event_type": EventType.BOOKING_CREATED.value},
                {"id": "dup", "label": "B", "event_type": EventType.MESSAGE_RECEIVED.value},
            ]
        )


def test_window_days_is_optional_and_bounded() -> None:
    m = MetricDefinitionSchema(id="m", label="L", event_type=EventType.BOOKING_CREATED.value)
    assert m.window_days is None, "None = eredita la finestra globale della dashboard"
    assert m.aggregation == "count", "V1: solo conteggi (i rate hanno denominatori eterogenei)"

    MetricDefinitionSchema(
        id="m", label="L", event_type=EventType.BOOKING_CREATED.value, window_days=7
    )
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(
            id="m", label="L", event_type=EventType.BOOKING_CREATED.value, window_days=0
        )
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(
            id="m", label="L", event_type=EventType.BOOKING_CREATED.value, window_days=400
        )


def test_unknown_metric_field_is_rejected() -> None:
    """`extra='forbid'`: un knob con typo va rifiutato in scrittura, non ignorato."""
    with pytest.raises(ValidationError):
        MetricDefinitionSchema(
            id="m",
            label="L",
            event_type=EventType.BOOKING_CREATED.value,
            aggregaton="count",  # typo volontario
        )


def test_dashboard_section_is_part_of_the_cascade_schema() -> None:
    """La chiave è nel bag tipato → esportata al FE via OpenAPI e validata in scrittura."""
    assert "dashboard" in BotConfigSchema.model_fields
    assert ConfigKey.DASHBOARD_METRICS.value == "dashboard.metrics"
