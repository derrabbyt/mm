"""Read-only mappings onto the event scraper's tables.

The activity-loader project owns `events` and `occurrences`: it creates them,
migrates them, and is the only writer. We map the columns we read and nothing
else, so a column added or dropped over there costs us nothing. `migrations/env.py`
lists both tables in SCRAPER_TABLES, which keeps Alembic autogenerate from
emitting DDL for them.

Never write through these models - until activity-loader moves into `jobs.py`
and this module takes the tables over.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...db.base import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Exactly one of the title/description pairs is filled, the one named by
    # lang_primary; the other side is written as an empty string, not NULL.
    lang_primary: Mapped[str] = mapped_column(Text, nullable=False)
    title_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The aggregator's own page for the event. Always set.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The organiser's page, when the scraper managed to find one - it usually
    # does not, so treat this as the nicer link rather than the reliable one.
    origin_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Hosted by the source, on 18 different domains - we hotlink rather than
    # mirror, so any of them can 404 or start refusing us at any time.
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    venue_name_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    street: Mapped[str | None] = mapped_column(Text, nullable=True)
    postcode: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Plain columns rather than a PostGIS geometry, and null for the ~5% of
    # events the scraper could not geocode.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Set once an event stops showing up in its source's listing.
    disappeared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Occurrence(Base):
    """One dated instance of an event.

    Not one row per day: a five-night run is five rows, but a museum open all
    year is a single row dated at its start with `duration_days` set to 364.
    Anything filtering by day has to treat this as a range.
    """

    __tablename__ = "occurrences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )

    # start_utc is what we compare against; start_local is the wall-clock time
    # to show, already in the venue's timezone.
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    start_local: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # The local calendar day, so "on this date" needs no timezone arithmetic.
    date_local: Mapped[date] = mapped_column(Date, nullable=False)

    # All-day rows are stamped midnight; their start time means nothing.
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Days the run covers beyond date_local. 0 for a single day, 364 for a
    # museum open all year - 750 rows carry a non-zero value.
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
