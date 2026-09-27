"""The scraped catalogue: what a Source said, and when it said it happens.

A **Listing** is one Source's record of a happening; an **Occurrence** is one
time range of a Listing. Several Sources describe the same real-world happening,
so a Listing is not yet what a person is shown - deduplicating them into Events
is a later step.

These tables live in the `content` schema rather than `public`, apart from the
application's own data, so the scraping job can connect as a role that cannot
reach accounts or meetups. See docs/adr/0001-content-schema.md.
"""

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
    """One Source's record of a happening.

    Identified across runs by `(source, source_ref)` - the id the Source
    itself uses - so a re-scrape updates a row rather than duplicating it.
    """

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_ref", name="uq_listings_source_ref"),
        Index("ix_listings_title_norm", "title_norm"),
        Index("ix_listings_venue_name_norm", "venue_name_norm"),
        Index("ix_listings_origin_url", "origin_url"),
        Index("ix_listings_position", "lat", "lon"),
        # Partial: every read filters on it, and the retired rows accumulate.
        Index(
            "ix_listings_live",
            "source",
            postgresql_where=text("disappeared_at IS NULL"),
        ),
        {"schema": CONTENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    source: Mapped[str] = mapped_column(Text, nullable=False)
    # The Source's own reference for the happening - not ours, and not an Event:
    # whether two of these describe one Event is decided much later. Stable
    # across runs for every Source that publishes one, and synthesised from the
    # page URL for those that do not.
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)

    # Exactly one of the title/description pairs is filled, the one named by
    # lang_primary; the other side is written as an empty string, not NULL.
    lang_primary: Mapped[str] = mapped_column(
        Text, nullable=False, default="de", server_default=text("'de'")
    )
    title_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Casefolded, punctuation-stripped forms the matcher compares. Stored rather
    # than computed per comparison: deduplication reads them once per run and
    # they are what the indexes above are for.
    title_norm: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    venue_name_norm: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The Source's own page for the happening. Always set.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The organiser's page, when the Source named one - it usually does not, so
    # treat this as the nicer link rather than the reliable one.
    origin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Hosted by the Source, on 18 different domains - we hotlink rather than
    # mirror, so any of them can 404 or start refusing us at any time.
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    venue_name_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    street: Mapped[str | None] = mapped_column(Text, nullable=True)
    postcode: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Plain columns rather than a PostGIS geometry, and null for the ~5% of
    # Listings nothing could be geocoded from.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Where the position came from - the Source itself, or the geocoder - and
    # how precisely the geocoder placed it.
    geo_source: Mapped[str] = mapped_column(
        Text, nullable=False, default="none", server_default=text("'none'")
    )
    geo_precision: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The Source's own category words, kept verbatim alongside the one bucket we
    # mapped them into, so a remapping can be redone without re-scraping.
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

    # What normalisation objected to without rejecting the row outright, so a
    # half-usable Listing carries its own caveats instead of being dropped.
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    first_seen_run: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_run: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set once a Listing stops showing up in its Source's output. Retired rather
    # than deleted: several Sources drop a cancelled happening silently, and the
    # difference between "cancelled" and "we failed to fetch" is worth keeping.
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
    """One time range of a Listing.

    Not one row per day: a five-night run is five rows, but a museum open all
    year is a single row dated at its start with `duration_days` set to 364.
    Anything filtering by day has to treat this as a range.
    """

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

    # The local calendar day, so "on this date" needs no timezone arithmetic.
    date_local: Mapped[date] = mapped_column(Date, nullable=False)
    # The evening a small-hours start belongs to: a 01:00 club night is the
    # night of the previous day, which is the day someone would look for it on.
    night_of: Mapped[date | None] = mapped_column(Date, nullable=True)

    # All-day rows are stamped midnight; their start time means nothing.
    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Days the run covers beyond date_local. 0 for a single day, 364 for a
    # museum open all year.
    duration_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    last_seen_run: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuarantinedListing(Base):
    """A scraped record normalisation refused, kept rather than dropped.

    Nothing reads this on the serving path. It exists so that "this Source
    returned 400 rows and 380 of them were unusable" is answerable afterwards,
    against the payload that actually arrived.
    """

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
    # The parsed record as it stood when normalisation gave up on it.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceRun(Base):
    """What one Source did in one run.

    One row per Source per run, written whether the Source succeeded or not, so
    "did this Source quietly stop returning anything?" has an answer that does
    not depend on anyone having watched the logs.
    """

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
    """One real-world happening on one day: what a person is actually shown.

    Built by grouping a day's Listings (see `dedup.py`) and rendering the
    richest of them, gap-filled from the rest. Entirely derived, and rebuilt
    by every run - so a column added here needs a migration but never a
    backfill, and there is no reason to carry a field before something reads
    it. Why these Listings are one Event is not stored either: the membership
    is, and the verdicts are reproducible from it with `dedup.compare`.

    The times are not here. An Event is an identity; when it is on is still
    the Occurrences' answer, reached through `event_listings`, so the two
    cannot drift apart.
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("date_local", "group_key", name="uq_events_day_group"),
        # The read filters on the day first and the distance second.
        Index("ix_events_day_range", "date_local", "duration_days"),
        Index("ix_events_position", "lat", "lon"),
        {"schema": CONTENT_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Names the group by the Listing that represents it, not by the set of its
    # members, so a third Source joining an Event does not renumber the id the
    # API has already served. It changes only when a richer Listing takes over.
    group_key: Mapped[str] = mapped_column(Text, nullable=False)

    # The day this Event is on, and how many days beyond it the run covers -
    # the same range an Occurrence carries, because that is where they come
    # from. A museum open all year is one Event, not 365.
    date_local: Mapped[date] = mapped_column(Date, nullable=False)
    duration_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # The Listing this Event renders as. Its links and its Source are the
    # Event's; everything else may be filled in from the other members.
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

    # Never null in practice: a group with no position at all is not built,
    # because the read filters on distance and could never return it.
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
    """Which Listings one Event was built from.

    Kept rather than implied, for two reasons: the read reaches the
    Occurrences through it, and it is what makes a merge reproducible after
    the run that decided it is over. Which member the Event reads as is not
    here - `events.primary_listing_id` is that, and one answer is enough.
    """

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
