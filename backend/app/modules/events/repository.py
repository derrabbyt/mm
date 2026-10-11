from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
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

from ...core.exceptions import (
    EventsLoadError,
    EventWriteError,
    ListingWriteError,
    ScrapeLockError,
)
from .models import (
    Event,
    EventListing,
    Listing,
    Occurrence,
    QuarantinedListing,
    SourceRun,
)
from .scraped import BuiltEvent, NormalizedListing, Rejected, SourceRunStats

# The name of the run lock. Postgres gives advisory locks one flat namespace
# across the whole database, so this number *is* the name: two runs only see
# each other if they ask about the same one, and changing it silently stops them
# doing so.
SCRAPE_LOCK_KEY = 8_531_002


@contextmanager
def scrape_lock(db: Session) -> Iterator[bool]:
    try:
        acquired = bool(
            db.execute(select(func.pg_try_advisory_lock(SCRAPE_LOCK_KEY))).scalar_one()
        )
    except SQLAlchemyError as exc:
        raise ScrapeLockError() from exc

    try:
        yield acquired
    finally:
        if acquired:
            with suppress(SQLAlchemyError):
                db.execute(select(func.pg_advisory_unlock(SCRAPE_LOCK_KEY)))


def _distance_meters(latitude: float, longitude: float) -> ColumnElement[float]:
    return func.ST_DistanceSphere(
        func.ST_MakePoint(Event.lon, Event.lat),
        func.ST_MakePoint(longitude, latitude),
    ).cast(Float)


def _covers_day(
    date_local: ColumnElement[date],
    duration_days: ColumnElement[int],
    day: date,
) -> ColumnElement[bool]:
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
    distance = _distance_meters(latitude, longitude)

    ongoing = Occurrence.date_local < day

    next_occurrence = (
        select(
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


_EVENT_KEYS = frozenset({"group_key", "date_local", "listing_ids"})
_EVENT_COLUMNS = tuple(
    name for name in BuiltEvent.model_fields if name not in _EVENT_KEYS
)


def write_events(
    db: Session, day: date, events: Sequence[BuiltEvent], run_id: str
) -> int:
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

        if written:
            db.execute(delete(EventListing).where(EventListing.event_id.in_(written)))
            db.execute(insert(EventListing), members)

        db.commit()
    except SQLAlchemyError as exc:
        raise EventWriteError(day.isoformat()) from exc

    return len(events)


def drop_events_not_built_by(
    db: Session, run_id: str, *, since: date, until: date
) -> int:
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
    return value.replace(tzinfo=None) if value is not None else None


def upsert_listing(db: Session, listing: NormalizedListing, run_id: str) -> int:
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


def listing_counts(
    db: Session, source: str, run_id: str, *, since: date, until: date
) -> tuple[int, int]:
    shows_in_window = (
        select(Occurrence.listing_id)
        .where(
            Occurrence.listing_id == Listing.id,
            Occurrence.date_local <= until,
            Occurrence.date_local + Occurrence.duration_days >= since,
        )
        .exists()
    )
    query = select(
        func.count(),
        func.count().filter(Listing.last_seen_run != run_id),
    ).where(
        Listing.source == source,
        Listing.disappeared_at.is_(None),
        shows_in_window,
    )
    try:
        live, unseen = db.execute(query).one()
    except SQLAlchemyError as exc:
        raise ListingWriteError(source) from exc
    return live, unseen


def retire_unseen(db: Session, source: str, run_id: str) -> int:
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
