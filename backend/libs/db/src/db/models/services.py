"""Servizi prenotabili e orari di apertura del merchant (UC-02 booking).

`Service` — catalogo dei servizi offerti dal merchant. Ogni servizio ha una
durata propria (sovrascrive `booking.default_duration_min`), un prezzo
opzionale (propagato come `monetary_value` su GHL) e può puntare a un
calendario GHL specifico diverso da quello di default.

`BusinessHour` — orari di apertura per giorno della settimana. Usati dal
booking handler per validare / filtrare gli slot prima di interrogare GHL,
e iniettati nel system prompt così il bot sa quando il merchant è disponibile.

`BusinessClosure` — chiusure eccezionali (festività, ferie). Hanno la
precedenza su `BusinessHour` per la data corrispondente.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, TimestampMixin, uuid_pk


class Service(Base, TimestampMixin):
    """Un servizio prenotabile offerto dal merchant.

    La durata (`duration_min`) viene usata dal booking handler per calcolare
    `end_time` dello slot GHL. Il `buffer_min` aggiunge tempo di blocco dopo
    l'appuntamento (pulizia, preparazione). Il prezzo è opzionale: se NULL il
    bot risponde "su richiesta" e non popola `monetary_value` su GHL.
    """

    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("merchant_id", "handle", name="uq_services_merchant_handle"),
        CheckConstraint("duration_min >= 5 AND duration_min <= 480", name="ck_services_duration"),
        CheckConstraint("buffer_min >= 0 AND buffer_min <= 120", name="ck_services_buffer"),
        CheckConstraint("price IS NULL OR price >= 0", name="ck_services_price"),
        Index("ix_services_merchant_active_sort", "merchant_id", "is_active", "sort_order"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Slug URL-safe: usato come chiave stabile nelle action payload e nell'UI.
    handle: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    # Tempo di blocco post-appuntamento (non visibile al cliente).
    buffer_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    # Se impostato, sovrascrive booking.default_calendar_id per questo servizio.
    ghl_calendar_id: Mapped[str | None] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class BusinessHour(Base):
    """Orario di apertura per un giorno della settimana (0=Lun … 6=Dom).

    `is_open=False` indica giorno di chiusura (i campi time vengono ignorati).
    `break_start`/`break_end` modellano la pausa pranzo: il booking handler
    tratta i due slot [open_time, break_start) e [break_end, close_time) come
    finestre prenotabili distinte. Se la pausa non è impostata, l'intera
    finestra [open_time, close_time) è disponibile.
    """

    __tablename__ = "business_hours"
    __table_args__ = (
        UniqueConstraint("merchant_id", "day_of_week", name="uq_business_hours_merchant_day"),
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_business_hours_dow"),
        CheckConstraint(
            "NOT is_open OR (open_time IS NOT NULL AND close_time IS NOT NULL AND open_time < close_time)",
            name="ck_business_hours_times",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 0=Lunedì, 6=Domenica (convenzione ISO weekday - 1).
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    open_time: Mapped[Any | None] = mapped_column(Time)
    close_time: Mapped[Any | None] = mapped_column(Time)
    break_start: Mapped[Any | None] = mapped_column(Time)
    break_end: Mapped[Any | None] = mapped_column(Time)


class BusinessClosure(Base, TimestampMixin):
    """Chiusura eccezionale per data specifica (festività, ferie, …).

    Prende precedenza su `BusinessHour` per il giorno corrispondente: il
    booking handler salta completamente la data prima di interrogare GHL.
    """

    __tablename__ = "business_closures"
    __table_args__ = (
        UniqueConstraint("merchant_id", "closed_on", name="uq_business_closures_merchant_date"),
        Index("ix_business_closures_merchant_date", "merchant_id", "closed_on"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    closed_on: Mapped[Any] = mapped_column(Date, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
