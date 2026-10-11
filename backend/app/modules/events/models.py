from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import CONTENT_SCHEMA, Base


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_ref", name="uq_listings_source_ref"),
        Index("ix_listings_title_norm", "title_norm"),
        Index("ix_listings_venue_name_norm", "venue_name_norm"),
        Index("ix_listings_origin_url", "origin_url"),
        Index("ix_listings_position", "lat", "lon"),
        Index(
            "ix_listings_live",
            "source",
            postgresql_where=text("disappeared_at IS NULL"),
        ),
        {"schema": CONTENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)

    lang_primary: Mapped[str] = mapped_column(
        Text, nullable=False, default="de", server_default=text("'de'")
    )
    title_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    title_norm: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    venue_name_norm: Mapped[str | None] = mapped_column(Text, nullable=True)

    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    venue_name_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    street: Mapped[str | None] = mapped_column(Text, nullable=True)
    postcode: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_source: Mapped[str] = mapped_column(
        Text, nullable=False, default="none", server_default=text("'none'")
    )
    geo_precision: Mapped[str | None] = mapped_column(Text, nullable=True)

    categories_raw: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    category: Mapped[str] = mapped_column(
        Text, nullable=False, default="other", server_default=text("'other'")
    )

    price_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_free: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    organizer: Mapped[str | None] = mapped_column(Text, nullable=True)

    warnings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    first_seen_run: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_run: Mapped[str | None] = mapped_column(Text, nullable=True)

    disappeared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Occurrence(Base):
    __tablename__ = "occurrences"
    __table_args__ = (
        UniqueConstraint(
            "listing_id", "start_utc", name="uq_occurrences_listing_start"
        ),
        Index("ix_occurrences_date_local", "date_local"),
        Index("ix_occurrences_night_of", "night_of"),
        Index("ix_occurrences_range", "start_utc", "end_utc"),
        {"schema": CONTENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    listing_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{CONTENT_SCHEMA}.listings.id", ondelete="CASCADE"),
        nullable=False,
    )

    # start_utc is what we compare against; start_local is the wall-clock time
    # to show, already in the Venue's timezone and deliberately naive - "the
    # show starts at 20:00" is a local fact, and a timestamptz would re-render
    # it in whatever session timezone the reader happens to have.
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    start_local: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_local: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    date_local: Mapped[date] = mapped_column(Date, nullable=False)
    night_of: Mapped[date | None] = mapped_column(Date, nullable=True)

    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    duration_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    last_seen_run: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuarantinedListing(Base):
    __tablename__ = "quarantine"
    __table_args__ = (
        Index("ix_quarantine_run_source", "run_id", "source"),
        {"schema": CONTENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceRun(Base):
    __tablename__ = "source_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "source", name="uq_source_runs_run_source"),
        Index("ix_source_runs_source_started", "source", text("started_at DESC")),
        {"schema": CONTENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    fetched_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    requests: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    raw_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    http_status_counts: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    parsed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    valid_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    quarantined_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    occurrences_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("date_local", "group_key", name="uq_events_day_group"),
        Index("ix_events_day_range", "date_local", "duration_days"),
        Index("ix_events_position", "lat", "lon"),
        {"schema": CONTENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    group_key: Mapped[str] = mapped_column(Text, nullable=False)

    date_local: Mapped[date] = mapped_column(Date, nullable=False)
    duration_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    primary_listing_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{CONTENT_SCHEMA}.listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)

    lang_primary: Mapped[str] = mapped_column(
        Text, nullable=False, default="de", server_default=text("'de'")
    )
    title_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    venue_name_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    street: Mapped[str | None] = mapped_column(Text, nullable=True)
    postcode: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    built_run: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EventListing(Base):
    __tablename__ = "event_listings"
    __table_args__ = (
        Index("ix_event_listings_listing_id", "listing_id"),
        {"schema": CONTENT_SCHEMA},
    )

    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{CONTENT_SCHEMA}.events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    listing_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{CONTENT_SCHEMA}.listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
