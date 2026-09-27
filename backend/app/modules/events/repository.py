"""Every SQLAlchemy statement the events module runs.

`SQLAlchemyError` never leaves this file: the read path raises
`EventsLoadError` and the write path `ListingWriteError`, so a service reads as
what it does rather than as error plumbing.

Two surfaces, and only one of them is served. Listings and their Occurrences
are what a scrape writes; **Events** are what a person is shown, rebuilt from
them after every run. The read touches Listings only through an Event's
membership, to reach the Occurrences that say when it is on.

Writes are upserts keyed on `(source, source_ref)`. Every touch stamps
`last_seen_run`, and that stamp is what says whether a row is still real:

* a **Listing** the run did not touch is retired with `disappeared_at` rather
  than deleted, because it may have been cancelled and several Sources simply
  drop a cancelled happening from their output - the difference between
  "cancelled" and "we failed to fetch" is worth keeping;
* an **Occurrence** its own Listing's run did not stamp is deleted, because the
  Listing is still there and the date simply moved.
"""

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Float,
    Row,
    and_,
    case,
    cast,
    delete,
    func,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.exceptions import EventsLoadError, EventWriteError, ListingWriteError
from .models import (
    Event,
    EventListing,
    Listing,
    Occurrence,
    QuarantinedListing,
    SourceRun,
)
from .scraped import BuiltEvent, NormalizedListing, Rejected, SourceRunStats


def _distance_meters(latitude: float, longitude: float) -> ColumnElement[float]:
    """Great-circle metres from every Event to this point. Events store plain
    lat/lon columns rather than a geometry, so there is no spatial index to hit;
    at a few thousand live rows the sequential scan has not been worth one."""
    return func.ST_DistanceSphere(
        func.ST_MakePoint(Event.lon, Event.lat),
        func.ST_MakePoint(longitude, latitude),
    ).cast(Float)


def _covers_day(
    date_local: ColumnElement[date],
    duration_days: ColumnElement[int],
    day: date,
) -> ColumnElement[bool]:
    """Does the range starting at `date_local` still include `day`?

    Both Occurrences and the Events built from them carry a start and a length
    rather than a row per day: a museum open all year is one of each, dated
    1 January with duration_days=364, so matching the start alone hides it for
    the other 364 days.
    """
    return and_(date_local <= day, date_local + duration_days >= day)


def find_events_near(
    db: Session,
    *,
    latitude: float,
    longitude: float,
    day: date,
    not_before: datetime,
    radius_meters: int,
    limit: int,
) -> Sequence[Row[tuple[Event, datetime, bool, float]]]:
    """Events showing on `day` within `radius_meters` of the point, nearest first.

    Each row is the Event, the start of the one Occurrence that matters,
    whether that Occurrence is all-day, and its distance in metres.
    """
    distance = _distance_meters(latitude, longitude)

    # True when the run began before today, so it is simply open rather than
    # starting at a particular time.
    ongoing = Occurrence.date_local < day

    # When an Event is on is still its Listings' Occurrences to say, reached
    # through the group's membership - denormalising a start time onto the
    # Event would be a second answer to the same question. LATERAL picks the
    # one showing that matters here: the next one that day, over every Listing
    # the Event was built from, which is also what drops an Event with nothing
    # on at all.
    next_occurrence = (
        select(
            # A run that began earlier has no meaningful start time today, so
            # report it as open all day rather than as starting in January.
            case((ongoing, cast(day, DateTime)), else_=Occurrence.start_local).label(
                "start_local"
            ),
            or_(Occurrence.all_day, ongoing).label("all_day"),
        )
        .select_from(EventListing)
        .join(Occurrence, Occurrence.listing_id == EventListing.listing_id)
        .where(
            EventListing.event_id == Event.id,
            _covers_day(Occurrence.date_local, Occurrence.duration_days, day),
            # An exhibition open all day is still worth showing at 20:00; a
            # concert that started at 18:00 is not. All-day rows carry a
            # midnight start that would otherwise fail this test every time,
            # and an ongoing run's start time belongs to a previous day.
            or_(Occurrence.all_day, ongoing, Occurrence.start_utc >= not_before),
        )
        .order_by(Occurrence.start_utc)
        .limit(1)
        .lateral("next_occurrence")
    )

    query = (
        select(
            Event,
            next_occurrence.c.start_local,
            next_occurrence.c.all_day,
            distance.label("distance_meters"),
        )
        .join(next_occurrence, true())
        .where(
            _covers_day(Event.date_local, Event.duration_days, day),
            Event.lat.is_not(None),
            Event.lon.is_not(None),
            distance <= radius_meters,
        )
        .order_by(distance, next_occurrence.c.start_local)
        .limit(limit)
    )

    try:
        return db.execute(query).all()
    except SQLAlchemyError as exc:
        raise EventsLoadError() from exc


def days_to_rebuild(db: Session, *, since: date, until: date) -> Sequence[date]:
    """Every local day that has a live Occurrence starting on it.

    A day is named by where its Occurrences *start*, which is how a year-long
    exhibition is still rebuilt in September: its day is 1 January, so the
    window is tested against the whole range an Occurrence covers rather than
    against its start alone.
    """
    query = (
        select(Occurrence.date_local)
        .join(Listing, Listing.id == Occurrence.listing_id)
        .where(
            Listing.disappeared_at.is_(None),
            Occurrence.date_local <= until,
            Occurrence.date_local + Occurrence.duration_days >= since,
        )
        .group_by(Occurrence.date_local)
        .order_by(Occurrence.date_local)
    )
    try:
        return db.execute(query).scalars().all()
    except SQLAlchemyError as exc:
        raise EventsLoadError() from exc


def day_candidates(
    db: Session, day: date
) -> Sequence[Row[tuple[Listing, bool, int, bool]]]:
    """Every live Listing with an Occurrence starting on `day`.

    One row per Listing, not per Occurrence: a Listing can hold several
    Occurrences on one day (eventfinder lists eight "Krimi Escape" sessions),
    and those are genuine showtimes rather than duplicates of each other. So
    `all_day` is aggregated with `bool_and` - a single timed showing makes the
    day timed - and the range is the longest of them.
    """
    query = (
        select(
            Listing,
            func.bool_and(Occurrence.all_day).label("all_day"),
            func.max(Occurrence.duration_days).label("duration_days"),
            func.bool_or(Occurrence.end_local.is_not(None)).label("end_known"),
        )
        .join(Occurrence, Occurrence.listing_id == Listing.id)
        .where(Listing.disappeared_at.is_(None), Occurrence.date_local == day)
        .group_by(Listing.id)
    )
    try:
        return db.execute(query).all()
    except SQLAlchemyError as exc:
        raise EventsLoadError() from exc


# Every field of a BuiltEvent that is a column of its own, which is all of
# them but the two identifying the row and the membership written beside it.
# Derived rather than listed again: unlike a Listing, an Event has nothing a
# run must leave alone, so a field added to one belongs in the other.
_EVENT_KEYS = frozenset({"group_key", "date_local", "listing_ids"})
_EVENT_COLUMNS = tuple(
    name for name in BuiltEvent.model_fields if name not in _EVENT_KEYS
)


def write_events(
    db: Session, day: date, events: Sequence[BuiltEvent], run_id: str
) -> int:
    """Store one day's Events. Returns how many were written.

    Upserted on `(date_local, group_key)` rather than replaced outright, so an
    Event keeps the id the API has already served for as long as the same
    Listing represents it. Clearing out what this run did not write is
    `drop_events_not_built_by`, once, at the end of the pass.
    """
    try:
        written: list[int] = []
        members: list[dict[str, object]] = []
        for built in events:
            values = built.model_dump(mode="json", include=set(_EVENT_COLUMNS))
            values |= {
                "group_key": built.group_key,
                "date_local": built.date_local,
                "built_run": run_id,
            }
            statement = insert(Event).values(**values)
            event_id = db.execute(
                statement.on_conflict_do_update(
                    index_elements=[Event.date_local, Event.group_key],
                    set_={
                        **{
                            column: statement.excluded[column]
                            for column in _EVENT_COLUMNS
                        },
                        "built_run": statement.excluded.built_run,
                        "updated_at": func.now(),
                    },
                ).returning(Event.id)
            ).scalar_one()
            written.append(event_id)
            members.extend(
                {
                    "event_id": event_id,
                    "listing_id": listing_id,
                    "is_primary": listing_id == built.primary_listing_id,
                }
                for listing_id in built.listing_ids
            )

        # Membership is replaced wholesale: which Listings an Event was built
        # from is decided from scratch every run, and a Listing that left the
        # group has to stop being one of its answers to "when is this on?".
        # In two statements for the whole day rather than two per Event, which
        # is the difference between three round trips per Event and one.
        if written:
            db.execute(delete(EventListing).where(EventListing.event_id.in_(written)))
            db.execute(insert(EventListing), members)

        # The last write of this day's transaction, so it is where it commits:
        # a day's Events and their membership land together or not at all.
        db.commit()
    except SQLAlchemyError as exc:
        raise EventWriteError(day.isoformat()) from exc

    return len(events)


def drop_events_not_built_by(
    db: Session, run_id: str, *, since: date, until: date
) -> int:
    """Delete the Events in the window that `run_id` did not write.

    A whole-window sweep rather than a delete per rebuilt day, because the
    days that need clearing are exactly the ones a per-day pass never visits:
    retiring a Listing leaves its Occurrences in place, so a day whose last
    Listing disappeared has no candidates to rebuild from and would go on
    being served off the back of them.

    The window is tested against the range an Event covers, not its start, so
    a year-long exhibition is swept with everything else.
    """
    try:
        result = db.execute(
            delete(Event).where(
                Event.built_run != run_id,
                Event.date_local <= until,
                Event.date_local + Event.duration_days >= since,
            )
        )
        db.commit()
    except SQLAlchemyError as exc:
        raise EventWriteError(f"{since}..{until}") from exc
    return result.rowcount


# Written on every touch. Geo is deliberately absent: a run with no coordinates
# for a Listing must not wipe the ones a previous run resolved.
_LISTING_COLUMNS = (
    "url",
    "origin_url",
    "title_de",
    "title_en",
    "description_de",
    "description_en",
    "lang_primary",
    "title_norm",
    "venue_name_norm",
    "venue_name_raw",
    "street",
    "postcode",
    "city",
    "country",
    "categories_raw",
    "category",
    "price_min",
    "price_max",
    "price_currency",
    "is_free",
    "ticket_url",
    "image_url",
    "organizer",
    "warnings",
)


def _naive(value: datetime | None) -> datetime | None:
    """Drop the offset from a wall-clock timestamp.

    `start_local` is a naive column. Handing the driver an aware datetime for it
    would make Postgres cast through the session timezone, silently shifting the
    very value the column exists to preserve.
    """
    return value.replace(tzinfo=None) if value is not None else None


def upsert_listing(db: Session, listing: NormalizedListing, run_id: str) -> int:
    """Store one Listing and its Occurrences. Returns the Listing's id."""
    values = listing.model_dump(mode="json", include=set(_LISTING_COLUMNS))
    values |= {
        "source": listing.source,
        "source_ref": listing.source_ref,
        "lat": listing.lat,
        "lon": listing.lon,
        "geo_source": listing.geo_source,
        "geo_precision": listing.geo_precision,
        "first_seen_run": run_id,
        "last_seen_run": run_id,
    }

    statement = insert(Listing).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[Listing.source, Listing.source_ref],
        set_={
            **{column: statement.excluded[column] for column in _LISTING_COLUMNS},
            # A position already resolved is not clobbered by a later run that
            # has none for the same Listing.
            "lat": func.coalesce(Listing.lat, statement.excluded.lat),
            "lon": func.coalesce(Listing.lon, statement.excluded.lon),
            "geo_source": case(
                (Listing.lat.is_not(None), Listing.geo_source),
                else_=statement.excluded.geo_source,
            ),
            "geo_precision": case(
                (Listing.lat.is_not(None), Listing.geo_precision),
                else_=statement.excluded.geo_precision,
            ),
            "last_seen_run": statement.excluded.last_seen_run,
            # Seen again, so it is back - a Source that briefly dropped a
            # happening and then listed it again must not stay retired.
            "disappeared_at": None,
            "updated_at": func.now(),
        },
    ).returning(Listing.id)

    try:
        listing_id = db.execute(statement).scalar_one()

        if listing.occurrences:
            rows = [
                {
                    "listing_id": listing_id,
                    "start_utc": occurrence.start_utc,
                    "end_utc": occurrence.end_utc,
                    "start_local": _naive(occurrence.start_local),
                    "end_local": _naive(occurrence.end_local),
                    "date_local": occurrence.date_local,
                    "night_of": occurrence.night_of,
                    "all_day": occurrence.all_day,
                    "duration_days": occurrence.duration_days,
                    "last_seen_run": run_id,
                }
                for occurrence in listing.occurrences
            ]
            occurrence_insert = insert(Occurrence).values(rows)
            db.execute(
                occurrence_insert.on_conflict_do_update(
                    index_elements=[Occurrence.listing_id, Occurrence.start_utc],
                    set_={
                        column: occurrence_insert.excluded[column]
                        for column in (
                            "end_utc",
                            "start_local",
                            "end_local",
                            "date_local",
                            "night_of",
                            "all_day",
                            "duration_days",
                            "last_seen_run",
                        )
                    },
                )
            )

            # Whatever this run did not stamp is a date the Source no longer
            # lists. Deleted rather than retired, unlike a Listing: a Listing
            # that stops appearing may have been cancelled, which is worth
            # keeping, but an Occurrence that vanished while its Listing stayed
            # is simply a date that moved - and leaving it behind serves the
            # happening on a day it is not on.
            db.execute(
                delete(Occurrence).where(
                    Occurrence.listing_id == listing_id,
                    Occurrence.last_seen_run != run_id,
                )
            )
    except SQLAlchemyError as exc:
        raise ListingWriteError(listing.source) from exc

    return listing_id


def quarantine(db: Session, rejected: Sequence[Rejected], run_id: str) -> int:
    """Keep the records normalisation refused. Returns how many were kept."""
    if not rejected:
        return 0
    try:
        db.execute(
            insert(QuarantinedListing),
            [
                {
                    "run_id": run_id,
                    "source": one.source,
                    "source_ref": one.source_ref,
                    "reason": one.reason,
                    "raw": one.raw,
                }
                for one in rejected
            ],
        )
    except SQLAlchemyError as exc:
        raise ListingWriteError(rejected[0].source) from exc
    return len(rejected)


def retire_unseen(db: Session, source: str, run_id: str) -> int:
    """Retire this Source's Listings that `run_id` did not touch.

    The caller must only reach here after a fetch that actually succeeded -
    otherwise a transient outage retires the Source's whole catalogue.
    """
    try:
        result = db.execute(
            update(Listing)
            .where(
                Listing.source == source,
                Listing.last_seen_run != run_id,
                Listing.disappeared_at.is_(None),
            )
            .values(disappeared_at=func.now())
        )
    except SQLAlchemyError as exc:
        raise ListingWriteError(source) from exc
    return result.rowcount


def record_source_run(db: Session, stats: SourceRunStats) -> None:
    """Store what one Source did in one run, replacing any earlier attempt.

    This is the last write of a Source's transaction, so it is where that
    transaction commits: a Source's Listings and the record of how it went land
    together or not at all.
    """
    values = stats.model_dump()
    statement = insert(SourceRun).values(**values)
    try:
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[SourceRun.run_id, SourceRun.source],
                set_={
                    column: statement.excluded[column]
                    for column in values
                    if column not in {"run_id", "source"}
                },
            )
        )
        db.commit()
    except SQLAlchemyError as exc:
        raise ListingWriteError(stats.source) from exc
