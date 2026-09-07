"""Every SQLAlchemy statement the events module runs.

`SQLAlchemyError` never leaves this file: the read path raises
`EventsLoadError` and the write path `ListingWriteError`, so a service reads as
what it does rather than as error plumbing.

Writes are upserts keyed on `(source, source_event_id)`, never
delete-and-replace. Every touch stamps `last_seen_run`, and a Listing that stops
appearing is retired with `disappeared_at` rather than deleted - which is what
makes a cancellation visible, since several Sources simply drop a cancelled
happening from their output.
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
    func,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.exceptions import EventsLoadError, ListingWriteError
from .models import Listing, Occurrence, QuarantinedListing, SourceRun
from .scraped import NormalizedListing, Rejected, SourceRunStats


def _distance_meters(latitude: float, longitude: float) -> ColumnElement[float]:
    """Great-circle metres from every Listing to this point. Listings store plain
    lat/lon columns rather than a geometry, so there is no spatial index to hit;
    at a few thousand live rows the sequential scan has not been worth one."""
    return func.ST_DistanceSphere(
        func.ST_MakePoint(Listing.lon, Listing.lat),
        func.ST_MakePoint(longitude, latitude),
    ).cast(Float)


def find_near(
    db: Session,
    *,
    latitude: float,
    longitude: float,
    day: date,
    not_before: datetime,
    radius_meters: int,
    limit: int,
) -> Sequence[Row[tuple[Listing, datetime, bool, float]]]:
    """Listings showing on `day` within `radius_meters` of the point, nearest first.

    Each row is the Listing, the start of the one Occurrence that matters,
    whether that Occurrence is all-day, and its distance in metres.
    """
    distance = _distance_meters(latitude, longitude)

    # An Occurrence is a *range*, not a day: a museum open all year is one row
    # dated 1 January with duration_days=364, so matching date_local alone hides
    # it for the other 364 days.
    covers_day = and_(
        Occurrence.date_local <= day,
        Occurrence.date_local + Occurrence.duration_days >= day,
    )
    # True when the run began before today, so it is simply open rather than
    # starting at a particular time.
    ongoing = Occurrence.date_local < day

    # An event runs on many dates; LATERAL picks the one showing that matters
    # here - the next one that day - and drops events with nothing on at all.
    next_occurrence = (
        select(
            # A run that began earlier has no meaningful start time today, so
            # report it as open all day rather than as starting in January.
            case((ongoing, cast(day, DateTime)), else_=Occurrence.start_local).label(
                "start_local"
            ),
            or_(Occurrence.all_day, ongoing).label("all_day"),
        )
        .where(
            Occurrence.listing_id == Listing.id,
            covers_day,
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
            Listing,
            next_occurrence.c.start_local,
            next_occurrence.c.all_day,
            distance.label("distance_meters"),
        )
        .join(next_occurrence, true())
        .where(
            Listing.disappeared_at.is_(None),
            Listing.lat.is_not(None),
            Listing.lon.is_not(None),
            distance <= radius_meters,
        )
        .order_by(distance, next_occurrence.c.start_local)
        .limit(limit)
    )

    try:
        return db.execute(query).all()
    except SQLAlchemyError as exc:
        raise EventsLoadError() from exc


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
        "source_event_id": listing.source_event_id,
        "lat": listing.lat,
        "lon": listing.lon,
        "geo_source": listing.geo_source,
        "geo_precision": listing.geo_precision,
        "first_seen_run": run_id,
        "last_seen_run": run_id,
    }

    statement = insert(Listing).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[Listing.source, Listing.source_event_id],
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
                    "source_event_id": one.source_event_id,
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
