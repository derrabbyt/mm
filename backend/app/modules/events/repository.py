"""Every SQLAlchemy statement the events module runs.

`SQLAlchemyError` never leaves this file.
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
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.exceptions import EventsLoadError
from .models import Listing, Occurrence


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
