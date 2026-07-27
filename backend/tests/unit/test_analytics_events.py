"""Guardrail del catalogo `analytics_events` (ADR 0021).

Il test più importante è `test_every_emitted_event_type_is_registered`: scansiona
il sorgente di produzione e pretende che ogni `event_type=` letterale passato a un
emit/costruzione ∈ :class:`EventType`. È la rete che avrebbe intercettato il bug
`reminder.sent` (reader) vs `appointment_reminder.sent` (emitter).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from db.analytics_events import (
    EVENT_CATALOG,
    EventCategory,
    EventType,
    event_catalog,
)

# backend/tests/unit/test_analytics_events.py -> parents[2] == backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_DIRS = ("libs", "workers", "services")

# Categorie che NON devono comparire tra le metriche di business offerte al merchant.
_NON_SELECTABLE_CATEGORIES = {EventCategory.ROLLUP, EventCategory.SYSTEM}


# --- Coerenza interna del catalogo ---------------------------------------


def test_event_catalog_covers_every_event_type() -> None:
    """Ogni membro di EventType ha esattamente una voce nel catalogo (e viceversa)."""
    assert set(EVENT_CATALOG) == set(EventType)
    assert len(EVENT_CATALOG) == len(EventType)


def test_catalog_entries_are_self_consistent() -> None:
    for event_type, definition in EVENT_CATALOG.items():
        assert definition.event_type is event_type, f"key/def mismatch for {event_type}"
        assert definition.label.strip(), f"missing label for {event_type}"
        assert definition.description.strip(), f"missing description for {event_type}"
        assert isinstance(definition.category, EventCategory)


def test_selectable_filter_excludes_operational_events() -> None:
    selectable = event_catalog(selectable_only=True)
    full = event_catalog()

    assert len(selectable) < len(full), "il filtro selectable deve escludere qualcosa"
    # Nessun rollup / evento di sistema tra le metriche selezionabili.
    assert all(d.category not in _NON_SELECTABLE_CATEGORIES for d in selectable)
    # I rollup interni non sono mai selezionabili come metrica.
    assert not EVENT_CATALOG[EventType.KPI_DAILY_CONVERSATIONS].selectable
    assert not EVENT_CATALOG[EventType.KB_REINDEXED].selectable
    # Le metriche di business chiave sì.
    assert EVENT_CATALOG[EventType.BOOKING_CREATED].selectable
    assert EVENT_CATALOG[EventType.PIPELINE_MOVED].selectable


# --- Regressione specifica del bug reminder.sent -------------------------


def test_reminder_event_type_matches_scheduler_emit() -> None:
    """Il valore del catalogo è ESATTAMENTE la stringa emessa dallo scheduler.

    Se qualcuno cambia una delle due, questo test (o il meta-scan sotto) rompe.
    """
    assert EventType.APPOINTMENT_REMINDER_SENT.value == "appointment_reminder.sent"


def test_kpi_reader_has_no_dead_reminder_string() -> None:
    """Le KPI non devono più leggere la stringa morta `reminder.sent` come literal.

    Cerca solo il literal quotato (il pattern del bug: `counts.get("reminder.sent")`);
    le menzioni in commento/docstring sono legittime.
    """
    source = (_BACKEND_ROOT / "libs/db/src/db/repositories/analytics.py").read_text()
    assert '"reminder.sent"' not in source


# --- Meta-scan: ogni event_type emesso è registrato ----------------------


def _iter_python_sources() -> Iterator[Path]:
    for rel in _SOURCE_DIRS:
        root = _BACKEND_ROOT / rel
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if "migrations" in parts or "tests" in parts or "__pycache__" in parts:
                continue
            yield path


def _string_constants(node: ast.expr) -> Iterator[str]:
    """Estrae le stringhe statiche da un'espressione (gestisce ternari e or/and)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.IfExp):
        yield from _string_constants(node.body)
        yield from _string_constants(node.orelse)
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _string_constants(value)
    # Name / Attribute / Call → non è un letterale statico: non verificabile, skip.


def _iter_emitted_event_types() -> Iterator[tuple[Path, str]]:
    for path in _iter_python_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "event_type":
                for literal in _string_constants(node.value):
                    yield path, literal


def test_every_emitted_event_type_is_registered() -> None:
    """Ogni `event_type=` letterale nel sorgente di produzione ∈ EventType."""
    valid = {e.value for e in EventType}
    found: set[str] = set()
    offenders: list[str] = []
    for path, literal in _iter_emitted_event_types():
        found.add(literal)
        if literal not in valid:
            offenders.append(f"{path.relative_to(_BACKEND_ROOT)}: {literal!r}")

    assert not offenders, "event_type non registrati in EventType:\n" + "\n".join(sorted(offenders))
    # Sanity: lo scan sta effettivamente trovando eventi (non un no-op silenzioso).
    assert len(found) >= 20, f"il meta-scan ha trovato solo {len(found)} event_type — sospetto"
