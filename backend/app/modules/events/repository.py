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
from .models import Event, Occurrence


def _distance_meters(latitude: float, longitude: float) -> ColumnElement[float]:
    """Great-circle metres from every event row to this point. The events table
    stores plain lat/lon columns, so there is no geometry index to hit; at a few
    thousand rows the sequential scan is not worth a schema we do not own."""
    return func.ST_DistanceSphere(
        func.ST_MakePoint(Event.lon, Event.lat),
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
) -> Sequence[Row[tuple[Event, datetime, bool, float]]]:
    """Events showing on `day` within `radius_meters` of the point, nearest first.

    Each row is the event, the start of the one occurrence that matters, whether
    that occurrence is all-day, and its distance in metres.
    """
    distance = _distance_meters(latitude, longitude)

    # An occurrence is a *range*, not a day: a museum open all year is one row
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
            Occurrence.event_id == Event.id,
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
            Event,
            next_occurrence.c.start_local,
            next_occurrence.c.all_day,
            distance.label("distance_meters"),
        )
        .join(next_occurrence, true())
        .where(
            Event.disappeared_at.is_(None),
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
