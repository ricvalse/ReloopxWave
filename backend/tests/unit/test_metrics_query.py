"""Logica di calcolo della dashboard configurabile (ADR 0021).

Testa gli helper puri dell'endpoint `GET /analytics/metrics`: raggruppamento per
finestra (= quante query servono), mappatura conteggi→metriche, ordine stabile.
"""

from __future__ import annotations

from api.routers.analytics import assemble_metric_values, group_metrics_by_window
from config_resolver import MetricDefinitionSchema
from db.analytics_events import EventType


def _m(id_: str, event_type: str, window: int | None = None) -> MetricDefinitionSchema:
    return MetricDefinitionSchema(
        id=id_, label=id_.replace("_", " ").title(), event_type=event_type, window_days=window
    )


# --- Raggruppamento per finestra -----------------------------------------


def test_metrics_without_window_share_one_query() -> None:
    """Caso comune: nessuna metrica ha window_days → una sola query."""
    defs = [
        _m("bookings", EventType.BOOKING_CREATED.value),
        _m("moved", EventType.PIPELINE_MOVED.value),
        _m("msgs", EventType.MESSAGE_RECEIVED.value),
    ]
    grouped = group_metrics_by_window(defs, since_days=30)
    assert list(grouped) == [30]
    assert len(grouped[30]) == 3


def test_custom_windows_split_into_separate_groups() -> None:
    defs = [
        _m("bookings", EventType.BOOKING_CREATED.value),
        _m("bookings_7d", EventType.BOOKING_CREATED.value, window=7),
        _m("moved_7d", EventType.PIPELINE_MOVED.value, window=7),
    ]
    grouped = group_metrics_by_window(defs, since_days=30)
    assert set(grouped) == {30, 7}
    assert [d.id for d in grouped[7]] == ["bookings_7d", "moved_7d"]
    assert [d.id for d in grouped[30]] == ["bookings"]


def test_empty_definitions_produce_no_queries() -> None:
    assert group_metrics_by_window([], since_days=30) == {}


# --- Assemblaggio dei valori ---------------------------------------------


def test_counts_are_mapped_onto_their_metric() -> None:
    defs = [
        _m("bookings", EventType.BOOKING_CREATED.value),
        _m("moved", EventType.PIPELINE_MOVED.value),
    ]
    counts = {30: {EventType.BOOKING_CREATED.value: 12, EventType.PIPELINE_MOVED.value: 5}}
    values = assemble_metric_values(defs, counts, since_days=30)
    assert [(v.id, v.value) for v in values] == [("bookings", 12), ("moved", 5)]
    assert all(v.window_days == 30 for v in values)


def test_missing_event_counts_as_zero_not_dropped() -> None:
    """Una metrica a zero resta in dashboard: un buco sarebbe un bug."""
    defs = [
        _m("bookings", EventType.BOOKING_CREATED.value),
        _m("never", EventType.RETENTION_PURGED.value),
    ]
    values = assemble_metric_values(defs, {30: {EventType.BOOKING_CREATED.value: 3}}, since_days=30)
    assert [(v.id, v.value) for v in values] == [("bookings", 3), ("never", 0)]


def test_each_metric_reads_its_own_window_bucket() -> None:
    """Due metriche sullo stesso evento ma finestre diverse leggono conteggi diversi."""
    defs = [
        _m("bookings_30d", EventType.BOOKING_CREATED.value),
        _m("bookings_7d", EventType.BOOKING_CREATED.value, window=7),
    ]
    counts = {
        30: {EventType.BOOKING_CREATED.value: 40},
        7: {EventType.BOOKING_CREATED.value: 9},
    }
    values = assemble_metric_values(defs, counts, since_days=30)
    assert [(v.id, v.value, v.window_days) for v in values] == [
        ("bookings_30d", 40, 30),
        ("bookings_7d", 9, 7),
    ]


def test_output_order_follows_configuration_not_counts() -> None:
    """La dashboard non deve ballare fra un refresh e l'altro."""
    defs = [
        _m("c_last", EventType.MESSAGE_REPLIED.value),
        _m("a_first", EventType.BOOKING_CREATED.value),
        _m("b_mid", EventType.PIPELINE_MOVED.value, window=7),
    ]
    counts = {
        30: {EventType.MESSAGE_REPLIED.value: 1, EventType.BOOKING_CREATED.value: 99},
        7: {EventType.PIPELINE_MOVED.value: 50},
    }
    values = assemble_metric_values(defs, counts, since_days=30)
    assert [v.id for v in values] == ["c_last", "a_first", "b_mid"]


def test_no_counts_at_all_yields_all_zeroes() -> None:
    defs = [_m("bookings", EventType.BOOKING_CREATED.value)]
    values = assemble_metric_values(defs, {}, since_days=30)
    assert [(v.id, v.value) for v in values] == [("bookings", 0)]
